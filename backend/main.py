from __future__ import annotations

import math
import os
import random
import re
import secrets
import statistics
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "SecureFlowDB").strip()

AUTO_TRAIN_ML = os.getenv("AUTO_TRAIN_ML", "false").lower() == "true"
ML_MAX_ROWS = int(os.getenv("ML_MAX_ROWS", "100000"))
ML_TREES = int(os.getenv("ML_TREES", "180"))
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "").strip()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
ENABLE_DEMO_RESET = os.getenv("ENABLE_DEMO_RESET", "false").lower() == "true"

FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Your architecture uses these exact total weights.
RISK_WEIGHTS = {
    "amount_risk": 10,
    "time_risk": 7,
    "frequency_risk": 12,
    "device_risk": 15,
    "location_risk": 15,
    "location_change_risk": 6,
    "beneficiary_risk": 12,
    "history_risk": 13,
    "typical_amount_risk": 10,
}

FEATURE_NAMES = [
    "amount_inr",
    "hours_since_last_txn",
    "transaction_count_24h",
    "user_avg_amount_prior",
    "amount_deviation_pct",
    "hour",
    "time_deviation_hours",
    "night_transaction",
    "known_beneficiary",
    "new_device",
    "unusual_location",
    "sudden_location_change",
    "unknown_beneficiary",
    "no_previous_transaction",
    "typical_amount_with_beneficiary",
]

EXCLUDED_FROM_RISK_SOURCE = {
    "synthetic_risk_scores",
    "synthetic_locations",
    "synthetic_incidents",
    "synthetic_notifications",
}


# ============================================================
# OPTIONAL ML
# ============================================================

RF_AVAILABLE = False
IF_AVAILABLE = False

RandomForestClassifier = None
IsolationForest = None
train_test_split = None
classification_report = None
roc_auc_score = None

try:
    from sklearn.ensemble import RandomForestClassifier, IsolationForest
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, roc_auc_score

    RF_AVAILABLE = True
    IF_AVAILABLE = True
except Exception:
    pass


# ============================================================
# GLOBAL STATE
# ============================================================

mongo_client: Optional[MongoClient] = None
db = None

ML_CLASSIFIER = None
ML_ANOMALY = None
ML_METRICS: dict[str, Any] = {
    "available": RF_AVAILABLE,
    "trained": False,
    "rows": 0,
    "labeled_rows": 0,
    "train_rows": 0,
    "test_rows": 0,
    "positive_rows": 0,
    "fraud_rate": 0.0,
    "roc_auc": None,
    "accuracy": None,
    "precision": None,
    "recall": None,
    "f1": None,
    "features": FEATURE_NAMES,
    "trained_at": None,
    "error": None,
    "model_type": "RandomForestClassifier + IsolationForest",
    "source_collection": "synthetic_transactions",
}


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mongo_client, db

    if not MONGODB_URI:
        print("WARNING: MONGODB_URI is not configured.")

    try:
        mongo_client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=20000,
        )

        mongo_client.admin.command("ping")
        db = mongo_client[MONGODB_DB]

        print(f"MongoDB connected: {MONGODB_DB}")

        ensure_indexes()

        if ML_MODEL_PATH:
            load_trained_model(ML_MODEL_PATH)
        elif AUTO_TRAIN_ML:
            train_ml_model()

    except Exception as exc:
        print(f"MongoDB startup error: {exc}")

    yield

    if mongo_client is not None:
        mongo_client.close()


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="SecureFlow-AI",
    description="AI Payment Fraud Defence Backend",
    version="8.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_demo_only_access(request, call_next):
    # The frontend still selects demo users by ID; real authentication and
    # object-level authorization are not implemented. Fail closed if somebody
    # attempts to deploy the prototype as a public production API.
    if APP_ENV != "development" and request.url.path not in {"/", "/health"}:
        return JSONResponse(
            status_code=503,
            content={"detail": "User authentication is required before public deployment."},
        )
    return await call_next(request)


# ============================================================
# DATABASE HELPERS
# ============================================================

def require_db():
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is not connected.",
        )
    return db


def require_admin_access(x_admin_key: Optional[str] = Header(default=None)) -> None:
    """Require a server-side key for privileged demo operations.

    This is not end-user authentication; do not put this key in React code.
    """
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin access is not configured.")
    if not secrets.compare_digest(x_admin_key or "", ADMIN_API_KEY):
        raise HTTPException(status_code=403, detail="Admin access denied.")


def collection_exists(name: str) -> bool:
    if db is None:
        return False

    try:
        return name in db.list_collection_names()
    except Exception:
        return False


def get_collection(name: str):
    database = require_db()
    return database[name]


def ensure_indexes():
    if db is None:
        return

    try:
        db["synthetic_transactions"].create_index(
            [("user_id", 1), ("transaction_time", -1)]
        )

        db["synthetic_devices"].create_index(
            [("user_id", 1)]
        )

        db["synthetic_dynamic_questions"].create_index(
            [("trigger_risk_factor", 1), ("active", 1)]
        )

        db["transactions"].create_index(
            [("user_id", 1), ("created_at", -1)]
        )

        db["verification_sessions"].create_index(
            [("transaction_id", 1)]
        )

        db["verification_events"].create_index(
            [("transaction_id", 1), ("created_at", -1)]
        )

        db["incidents"].create_index(
            [("created_at", -1)]
        )

        db["notifications"].create_index(
            [("user_id", 1), ("created_at", -1)]
        )

        db["reports"].create_index(
            [("created_at", -1)]
        )

    except Exception as exc:
        print(f"Index creation warning: {exc}")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    """
    Convert MongoDB/Python values into JSON-safe values.
    """

    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
            if k != "_id"
        }

    if isinstance(value, list):
        return [json_safe(v) for v in value]

    try:
        from bson import ObjectId

        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return 0.0

    return value


def id_variants(value: Any) -> list[Any]:
    """
    Allows user IDs such as:
        1
        "1"
    to match the database.
    """

    values = []

    if value is None:
        return values

    values.append(value)

    text = str(value).strip()

    if text not in values:
        values.append(text)

    if text.isdigit():
        number = int(text)
        if number not in values:
            values.append(number)

    return values


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()

    text = text.replace("&", " and ")

    text = re.sub(r"[^a-z0-9@./ -]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()

        if not text:
            return None

        try:
            text = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def first_value(document: dict, *keys: str, default=None):
    for key in keys:
        if key in document and document[key] not in (None, ""):
            return document[key]

    return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# ============================================================
# USER / DEVICE / TRANSACTION DATA
# ============================================================

def find_user(user_id: Any) -> Optional[dict]:
    collection = get_collection("synthetic_users")

    variants = id_variants(user_id)

    query = {
        "$or": [
            {"user_id": value}
            for value in variants
        ]
    }

    return collection.find_one(query)


def get_all_users() -> list[dict]:
    collection = get_collection("synthetic_users")

    documents = list(
        collection.find({})
        .sort("user_id", 1)
    )

    return [json_safe(doc) for doc in documents]


def get_user_transactions(
    user_id: Any,
    limit: int = 10000,
    include_all_operational: bool = False,
) -> list[dict]:

    collection = get_collection("synthetic_transactions")

    variants = id_variants(user_id)

    query = {
        "$or": [
            {"user_id": value}
            for value in variants
        ]
    }

    documents = list(
        collection.find(query)
        .sort("transaction_time", DESCENDING)
        .limit(limit)
    )

    # New, successfully processed demo payments can inform future behavior.
    # Rejected and unverified attempts must not become the user's "normal".
    if collection_exists("transactions"):
        operational = list(
            get_collection("transactions").find(query)
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        if not include_all_operational:
            trusted_outcomes = {
                "allow", "allowed", "monitor", "monitored", "completed", "success"
            }
            operational = [
                tx for tx in operational
                if normalize_text(first_value(tx, "status", "decision"))
                in trusted_outcomes
            ]
        documents.extend(operational)

    documents.sort(
        key=lambda tx: parse_datetime(first_value(
            tx, "transaction_time", "created_at", "time"
        )) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return documents[:limit]


def get_all_historical_transactions(
    limit: int = ML_MAX_ROWS,
) -> list[dict]:

    collection = get_collection("synthetic_transactions")

    return list(
        collection.find({})
        .limit(limit)
    )


def get_user_devices(user_id: Any) -> list[dict]:
    collection_name = "synthetic_devices"

    if not collection_exists(collection_name):
        return []

    collection = get_collection(collection_name)

    variants = id_variants(user_id)

    query = {
        "$or": [
            {"user_id": value}
            for value in variants
        ]
    }

    return list(collection.find(query))


def historical_location_names(
    transactions: list[dict],
) -> set[str]:

    locations = set()

    for tx in transactions:
        value = first_value(
            tx,
            "location_name",
            "location",
            "city",
            "location_label",
        )

        if value:
            locations.add(normalize_text(value))

    return locations


def historical_device_names(
    transactions: list[dict],
    devices: list[dict],
) -> set[str]:

    values = set()

    for tx in transactions:
        value = first_value(
            tx,
            "sender_device_id",
            "device_id",
            "device",
        )

        if value:
            values.add(normalize_text(value))

    for device in devices:
        for key in (
            "device_id",
            "device_name",
            "device",
            "sender_device_id",
        ):
            value = device.get(key)

            if value:
                values.add(normalize_text(value))

    return values


def historical_beneficiaries(
    transactions: list[dict],
) -> set[str]:

    values = set()

    for tx in transactions:
        beneficiary_id = first_value(
            tx,
            "beneficiary_id",
            "recipient_id",
        )

        if beneficiary_id is not None:
            values.add(str(beneficiary_id))

        upi = first_value(
            tx,
            "recipient_upi_id",
            "upi_id",
            "upi",
            "beneficiary_upi_id",
        )

        if upi:
            values.add(normalize_text(upi))

    return values


# ============================================================
# BENEFICIARY RESOLUTION
# ============================================================

BENEFICIARY_COLLECTIONS = [
    "synthetic_beneficiary_details",
    "synthetic_beneficiaries",
    "beneficiaries",
    "user_beneficiary_relationships",
    "beneficiary_relations",
]


def resolve_beneficiary(
    user_id: Any,
    recipient_upi_id: Optional[str],
    provided_beneficiary_id: Optional[Any] = None,
) -> dict:

    result = {
        "resolved": False,
        "beneficiary_id": None,
        "recipient_upi_id": recipient_upi_id,
        "document": None,
    }

    # The client cannot prove recipient trust by inventing an internal ID.
    # A known beneficiary must be resolved from server-held records/history.

    if not recipient_upi_id:
        return result

    normalized_upi = normalize_text(recipient_upi_id)

    for collection_name in BENEFICIARY_COLLECTIONS:

        if not collection_exists(collection_name):
            continue

        collection = get_collection(collection_name)

        candidates = [
            {"upi_id": recipient_upi_id},
            {"upi": recipient_upi_id},
            {"recipient_upi_id": recipient_upi_id},
            {"beneficiary_upi_id": recipient_upi_id},
        ]

        for query in candidates:

            try:
                document = collection.find_one(query)

                if document:
                    beneficiary_id = first_value(
                        document,
                        "beneficiary_id",
                        "recipient_id",
                        "id",
                    )

                    if beneficiary_id is not None:
                        result["beneficiary_id"] = beneficiary_id

                    result["resolved"] = True
                    result["document"] = document

                    return result

            except Exception:
                continue

    # A transaction history may contain the UPI directly.
    history = get_user_transactions(user_id)

    for tx in history:

        historical_upi = first_value(
            tx,
            "recipient_upi_id",
            "upi_id",
            "upi",
            "beneficiary_upi_id",
        )

        if historical_upi and normalize_text(historical_upi) == normalized_upi:

            beneficiary_id = first_value(
                tx,
                "beneficiary_id",
                "recipient_id",
            )

            result["beneficiary_id"] = beneficiary_id
            result["resolved"] = beneficiary_id is not None

            return result

    return result


# ============================================================
# USER BEHAVIOUR PROFILE
# ============================================================

def build_user_profile(
    user_id: Any,
    transactions: Optional[list[dict]] = None,
) -> dict:

    user = find_user(user_id)

    if user is None:
        raise HTTPException(
            status_code=404,
            detail=f"User {user_id} was not found.",
        )

    if transactions is None:
        transactions = get_user_transactions(user_id)

    amounts = [
        safe_float(tx.get("amount"))
        for tx in transactions
        if safe_float(tx.get("amount")) > 0
    ]

    times = []

    for tx in transactions:
        dt = parse_datetime(
            first_value(
                tx,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if dt:
            times.append(dt)

    locations = historical_location_names(transactions)

    devices = get_user_devices(user_id)
    device_names = historical_device_names(transactions, devices)

    beneficiary_values = historical_beneficiaries(transactions)

    average_amount = (
        statistics.mean(amounts)
        if amounts
        else 0.0
    )

    median_amount = (
        statistics.median(amounts)
        if amounts
        else 0.0
    )

    normal_hours = []

    for dt in times:
        normal_hours.append(dt.hour)

    average_hour = (
        statistics.mean(normal_hours)
        if normal_hours
        else 12.0
    )

    hour_counts = Counter(normal_hours)

    common_hours = [
        hour
        for hour, count in hour_counts.items()
        if count >= max(1, len(normal_hours) // 10)
    ]

    category_counts = Counter()

    for tx in transactions:
        category = first_value(
            tx,
            "transaction_category",
            "category",
        )

        if category:
            category_counts[str(category)] += 1

    return {
        "user": user,
        "transactions": transactions,
        "transaction_count": len(transactions),
        "average_amount": average_amount,
        "median_amount": median_amount,
        "min_amount": min(amounts) if amounts else 0.0,
        "max_amount": max(amounts) if amounts else 0.0,
        "average_hour": average_hour,
        "common_hours": common_hours,
        "familiar_locations": sorted(locations),
        "known_devices": sorted(device_names),
        "known_beneficiaries": sorted(beneficiary_values),
        "category_counts": dict(category_counts),
    }


# ============================================================
# DISTANCE / LOCATION HELPERS
# ============================================================

def haversine_km(
    lat1: Optional[float],
    lon1: Optional[float],
    lat2: Optional[float],
    lon2: Optional[float],
) -> Optional[float]:

    if None in (lat1, lon1, lat2, lon2):
        return None

    radius = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return 2 * radius * math.asin(math.sqrt(a))


def extract_coordinates(data: Any):
    if not isinstance(data, dict):
        return None, None

    lat = first_value(
        data,
        "latitude",
        "lat",
    )

    lon = first_value(
        data,
        "longitude",
        "lon",
        "lng",
    )

    if lat is None or lon is None:
        return None, None

    return safe_float(lat), safe_float(lon)


# ============================================================
# RISK FEATURE ENGINE
# ============================================================

def select_previous_transaction(
    transactions: list[dict],
    current_time: datetime,
) -> Optional[dict]:

    previous = []

    for tx in transactions:

        dt = parse_datetime(
            first_value(
                tx,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if dt and dt < current_time:
            previous.append((dt, tx))

    if not previous:
        return None

    previous.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return previous[0][1]


def amount_risk(
    amount: float,
    profile: dict,
) -> tuple[int, dict]:

    avg = profile["average_amount"]
    median = profile["median_amount"]

    deviation_points = 0
    high_value_points = 0
    round_amount_points = 0

    if avg > 0:
        deviation_pct = abs(amount - avg) / avg * 100

        if deviation_pct >= 100:
            deviation_points = 4
        elif deviation_pct >= 50:
            deviation_points = 2
        elif deviation_pct >= 30:
            deviation_points = 1

    if median > 0 and amount >= median * 5:
        high_value_points = 3
    elif avg > 0 and amount >= avg * 4:
        high_value_points = 2

    if amount >= 1000 and amount % 1000 == 0:
        round_amount_points = 3
    elif amount >= 500 and amount % 500 == 0:
        round_amount_points = 2

    total = min(
        10,
        deviation_points
        + high_value_points
        + round_amount_points,
    )

    return total, {
        "amount_deviation": deviation_points,
        "very_high_value": high_value_points,
        "unusual_round_amount": round_amount_points,
    }


def time_risk(
    current_time: datetime,
    profile: dict,
) -> tuple[int, dict]:

    unusual_hour_points = 0
    outside_normal_points = 0
    timing_change_points = 0

    hour = current_time.hour

    if hour < 6 or hour >= 23:
        unusual_hour_points = 3
    elif hour < 7 or hour >= 22:
        unusual_hour_points = 2

    common_hours = profile["common_hours"]

    if common_hours:

        distances = [
            min(abs(hour - h), 24 - abs(hour - h))
            for h in common_hours
        ]

        nearest = min(distances)

        if nearest >= 5:
            outside_normal_points = 2
        elif nearest >= 3:
            outside_normal_points = 1

    transactions = profile["transactions"]

    previous_times = []

    for tx in transactions:

        dt = parse_datetime(
            first_value(
                tx,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if dt and dt < current_time:
            previous_times.append(dt)

    if previous_times:

        previous_times.sort(reverse=True)

        previous = previous_times[0]

        previous_hour = previous.hour

        difference = min(
            abs(hour - previous_hour),
            24 - abs(hour - previous_hour),
        )

        if difference >= 8:
            timing_change_points = 2
        elif difference >= 5:
            timing_change_points = 1

    total = min(
        7,
        unusual_hour_points
        + outside_normal_points
        + timing_change_points,
    )

    return total, {
        "unusual_hour": unusual_hour_points,
        "outside_normal_active_time": outside_normal_points,
        "timing_pattern_change": timing_change_points,
    }


def frequency_risk(
    user_id: Any,
    current_time: datetime,
    transactions: list[dict],
    beneficiary_id: Any = None,
) -> tuple[int, dict]:

    recent_1h = 0
    recent_24h = 0
    same_beneficiary_30m = 0

    for tx in transactions:

        dt = parse_datetime(
            first_value(
                tx,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if not dt or dt >= current_time:
            continue

        age = current_time - dt

        if age <= timedelta(hours=1):
            recent_1h += 1

        if age <= timedelta(hours=24):
            recent_24h += 1

        if (
            beneficiary_id is not None
            and str(first_value(
                tx,
                "beneficiary_id",
                "recipient_id",
                default=""
            )) == str(beneficiary_id)
            and age <= timedelta(minutes=30)
        ):
            same_beneficiary_30m += 1

    too_many_points = 5 if recent_1h >= 3 else (
        3 if recent_1h == 2 else 0
    )

    # A healthy user can have some activity. The trigger is
    # deliberately relative rather than using a fixed "fraud" number.
    daily_average = max(
        1,
        len(transactions) / max(
            1,
            len({
                parse_datetime(
                    first_value(
                        tx,
                        "transaction_time",
                        "created_at",
                        "time",
                    )
                ).date()
                for tx in transactions
                if parse_datetime(
                    first_value(
                        tx,
                        "transaction_time",
                        "created_at",
                        "time",
                    )
                )
            })
        ),
    )

    sudden_increase_points = 0

    if recent_24h >= max(6, daily_average * 2.0):
        sudden_increase_points = 4
    elif recent_24h >= max(4, daily_average * 1.5):
        sudden_increase_points = 2

    repeated_points = 3 if same_beneficiary_30m >= 2 else 0

    total = min(
        12,
        too_many_points
        + sudden_increase_points
        + repeated_points,
    )

    return total, {
        "too_many_short_period": too_many_points,
        "sudden_frequency_increase": sudden_increase_points,
        "rapid_repeated_transactions": repeated_points,
    }


def device_risk(
    current_device: str,
    profile: dict,
    current_time: datetime,
    amount: float,
    location_unusual: bool,
) -> tuple[int, dict]:

    current = normalize_text(current_device)

    # An unavailable signal is not evidence of a new or compromised device.
    if not current or current == "unknown-device":
        return 0, {
            "new_unrecognized_device": 0,
            "device_not_associated": 0,
            "new_device_unusual_activity": 0,
            "known_device": False,
            "signal_available": False,
        }

    known_devices = profile["known_devices"]

    is_known = current in known_devices if current else False

    first_transaction_points = 0
    association_points = 0
    unusual_device_activity_points = 0

    if not current or not is_known:
        first_transaction_points = 6
        association_points = 5

    if (
        (not is_known)
        and (
            amount > max(
                profile["average_amount"] * 2,
                10000,
            )
            or location_unusual
            or current_time.hour < 6
            or current_time.hour >= 23
        )
    ):
        unusual_device_activity_points = 4

    total = min(
        15,
        first_transaction_points
        + association_points
        + unusual_device_activity_points,
    )

    return total, {
        "new_unrecognized_device": first_transaction_points,
        "device_not_associated": association_points,
        "new_device_unusual_activity": unusual_device_activity_points,
        "known_device": is_known,
    }


def location_risk(
    location_name: Optional[str],
    profile: dict,
    amount: float,
    device_score: int,
) -> tuple[int, dict]:

    current_location = normalize_text(location_name)

    if not current_location:
        return 0, {
            "new_unrecognized_location": 0,
            "significantly_different_location": 0,
            "location_with_unusual_activity": 0,
            "signal_available": False,
        }

    familiar_locations = {
        normalize_text(value)
        for value in profile["familiar_locations"]
    }

    new_location_points = 0
    different_location_points = 0
    combined_points = 0

    if not current_location:
        new_location_points = 5
    elif current_location not in familiar_locations:
        new_location_points = 5

        if familiar_locations:
            different_location_points = 5

    if (
        (new_location_points > 0)
        and (
            amount > max(
                profile["average_amount"] * 2,
                10000,
            )
            or device_score >= 6
        )
    ):
        combined_points = 5

    total = min(
        15,
        new_location_points
        + different_location_points
        + combined_points,
    )

    return total, {
        "new_unrecognized_location": new_location_points,
        "significantly_different_location": different_location_points,
        "location_with_unusual_activity": combined_points,
    }


def sudden_location_change_risk(
    current_location: Optional[str],
    current_lat: Optional[float],
    current_lon: Optional[float],
    current_time: datetime,
    transactions: list[dict],
) -> tuple[int, dict]:

    previous = select_previous_transaction(
        transactions,
        current_time,
    )

    if not previous:
        return 0, {
            "large_distance": 0,
            "unusually_fast_movement": 0,
            "historical_travel_inconsistency": 0,
        }

    previous_lat = safe_float(
        first_value(
            previous,
            "latitude",
            "lat",
            default=0,
        ),
        0,
    )

    previous_lon = safe_float(
        first_value(
            previous,
            "longitude",
            "lon",
            "lng",
            default=0,
        ),
        0,
    )

    previous_time = parse_datetime(
        first_value(
            previous,
            "transaction_time",
            "created_at",
            "time",
        )
    )

    distance_points = 0
    speed_points = 0
    historical_points = 0

    distance = haversine_km(
        previous_lat if previous_lat else None,
        previous_lon if previous_lon else None,
        current_lat,
        current_lon,
    )

    if distance is not None:

        if distance >= 100:
            distance_points = 2
        elif distance >= 50:
            distance_points = 1

        if previous_time:

            hours = max(
                (current_time - previous_time).total_seconds() / 3600,
                0.01,
            )

            speed = distance / hours

            if speed >= 500:
                speed_points = 2
            elif speed >= 200:
                speed_points = 1

    current_location_normalized = normalize_text(
        current_location
    )

    previous_location = normalize_text(
        first_value(
            previous,
            "location_name",
            "location",
            default="",
        )
    )

    if (
        current_location_normalized
        and previous_location
        and current_location_normalized != previous_location
        and distance is None
    ):
        historical_points = 2

    total = min(
        6,
        distance_points
        + speed_points
        + historical_points,
    )

    return total, {
        "large_distance": distance_points,
        "unusually_fast_movement": speed_points,
        "historical_travel_inconsistency": historical_points,
        "distance_km": distance,
    }


def beneficiary_risk(
    beneficiary_info: dict,
    transactions: list[dict],
    beneficiary_id: Any,
    current_time: datetime,
) -> tuple[int, dict]:

    first_time_points = 0
    low_trust_points = 0
    suspicious_points = 0

    previous_beneficiary_transactions = []

    for tx in transactions:

        tx_beneficiary = first_value(
            tx,
            "beneficiary_id",
            "recipient_id",
        )

        if (
            beneficiary_id is not None
            and tx_beneficiary is not None
            and str(tx_beneficiary) == str(beneficiary_id)
        ):
            previous_beneficiary_transactions.append(tx)

    if not previous_beneficiary_transactions:
        first_time_points = 4
        low_trust_points = 4

    document = beneficiary_info.get("document")

    if document:

        trust = first_value(
            document,
            "trust_score",
            "beneficiary_trust_score",
            default=50,
        )

        trust = safe_float(trust, 50)

        if trust < 30:
            low_trust_points = 4
        elif trust < 50:
            low_trust_points = max(
                low_trust_points,
                2,
            )

        reports = safe_int(
            first_value(
                document,
                "fraud_reports",
                "fraud_report_count",
                default=0,
            )
        )

        if reports > 0:
            suspicious_points = 4

    # If history contains held/blocked transactions for this
    # beneficiary, treat the beneficiary as suspicious.
    for tx in previous_beneficiary_transactions:

        status = normalize_text(
            tx.get("status")
        )

        if status in {
            "held",
            "blocked",
            "fraud",
            "rejected",
            "declined",
        }:
            suspicious_points = 4
            break

    total = min(
        12,
        first_time_points
        + low_trust_points
        + suspicious_points,
    )

    return total, {
        "first_time_unknown_beneficiary": first_time_points,
        "low_beneficiary_trust": low_trust_points,
        "suspicious_beneficiary_behavior": suspicious_points,
        "previous_beneficiary_transactions": len(
            previous_beneficiary_transactions
        ),
    }


def history_risk(
    beneficiary_id: Any,
    transactions: list[dict],
    current_location: Optional[str],
    current_device: Optional[str],
) -> tuple[int, dict]:

    previous_beneficiary = []

    for tx in transactions:

        tx_beneficiary = first_value(
            tx,
            "beneficiary_id",
            "recipient_id",
        )

        if (
            beneficiary_id is not None
            and tx_beneficiary is not None
            and str(tx_beneficiary) == str(beneficiary_id)
        ):
            previous_beneficiary.append(tx)

    no_history_points = 5 if not previous_beneficiary else 0

    limited_context_points = 0

    if previous_beneficiary:

        known_locations = {
            normalize_text(
                first_value(
                    tx,
                    "location_name",
                    "location",
                    default="",
                )
            )
            for tx in previous_beneficiary
        }

        known_devices = {
            normalize_text(
                first_value(
                    tx,
                    "sender_device_id",
                    "device_id",
                    "device",
                    default="",
                )
            )
            for tx in previous_beneficiary
        }

        if (
            normalize_text(current_location)
            not in known_locations
            or normalize_text(current_device)
            not in known_devices
        ):
            limited_context_points = 4

    no_comparable_pattern_points = 0

    if len(previous_beneficiary) == 0:
        no_comparable_pattern_points = 4
    elif len(previous_beneficiary) == 1:
        no_comparable_pattern_points = 2

    total = min(
        13,
        no_history_points
        + limited_context_points
        + no_comparable_pattern_points,
    )

    return total, {
        "no_user_beneficiary_history": no_history_points,
        "limited_device_location_context": limited_context_points,
        "no_comparable_pattern": no_comparable_pattern_points,
    }


def typical_amount_risk(
    amount: float,
    beneficiary_id: Any,
    transactions: list[dict],
    category: Optional[str],
) -> tuple[int, dict]:

    beneficiary_transactions = []

    for tx in transactions:

        tx_beneficiary = first_value(
            tx,
            "beneficiary_id",
            "recipient_id",
        )

        if (
            beneficiary_id is not None
            and tx_beneficiary is not None
            and str(tx_beneficiary) == str(beneficiary_id)
        ):
            beneficiary_transactions.append(tx)

    user_beneficiary_points = 0
    beneficiary_amount_points = 0
    category_points = 0

    amounts = [
        safe_float(tx.get("amount"))
        for tx in beneficiary_transactions
        if safe_float(tx.get("amount")) > 0
    ]

    if amounts:

        average = statistics.mean(amounts)

        if average > 0:

            deviation = abs(amount - average) / average

            if deviation >= 1:
                user_beneficiary_points = 4
            elif deviation >= 0.5:
                user_beneficiary_points = 2

            if amount > average * 3:
                beneficiary_amount_points = 3
            elif amount > average * 2:
                beneficiary_amount_points = 2

        categories = [
            normalize_text(
                first_value(
                    tx,
                    "transaction_category",
                    "category",
                    default="",
                )
            )
            for tx in beneficiary_transactions
        ]

        if (
            category
            and categories
            and normalize_text(category)
            not in categories
        ):
            category_points = 3

    else:

        # No beneficiary history means we cannot establish
        # a normal beneficiary amount.
        user_beneficiary_points = 4
        beneficiary_amount_points = 3

        if category:
            category_points = 3

    total = min(
        10,
        user_beneficiary_points
        + beneficiary_amount_points
        + category_points,
    )

    return total, {
        "user_beneficiary_amount_deviation": user_beneficiary_points,
        "beneficiary_normal_amount_deviation": beneficiary_amount_points,
        "unusual_beneficiary_category": category_points,
    }


# ============================================================
# COMPLETE RISK ENGINE
# ============================================================

def combine_risk_scores(rule_score: float, ml_probability: Optional[float]) -> float:
    """Allow supervised ML to raise risk without overriding hard rule flags."""
    if ml_probability is None:
        return round(max(0.0, min(100.0, rule_score)), 2)
    blended = rule_score * 0.75 + ml_probability * 0.25
    return round(max(0.0, min(100.0, max(rule_score, blended))), 2)


def calculate_risk(
    user_id: Any,
    amount: float,
    transaction_time: datetime,
    transaction_category: Optional[str],
    transaction_purpose: Optional[str],
    device: Optional[str],
    location_name: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    recipient_upi_id: Optional[str],
    beneficiary_id: Any = None,
) -> dict:

    # A backdated simulation must not learn from later transactions.
    history = [
        tx for tx in get_user_transactions(user_id)
        if (
            (timestamp := parse_datetime(first_value(
                tx, "transaction_time", "created_at", "time"
            ))) is not None
            and timestamp < transaction_time
        )
    ]

    profile = build_user_profile(
        user_id,
        history,
    )

    beneficiary_info = resolve_beneficiary(
        user_id,
        recipient_upi_id,
        beneficiary_id,
    )

    beneficiary_id = beneficiary_info.get(
        "beneficiary_id"
    )

    amount_points, amount_breakdown = amount_risk(
        amount,
        profile,
    )

    time_points, time_breakdown = time_risk(
        transaction_time,
        profile,
    )

    # Location is calculated first because device risk also
    # considers unusual activity.
    location_points, location_breakdown = location_risk(
        location_name,
        profile,
        amount,
        0,
    )

    device_points, device_breakdown = device_risk(
        device or "",
        profile,
        transaction_time,
        amount,
        location_points > 0,
    )

    # Recalculate location combined activity using actual
    # device risk.
    location_points, location_breakdown = location_risk(
        location_name,
        profile,
        amount,
        device_points,
    )

    frequency_points, frequency_breakdown = frequency_risk(
        user_id,
        transaction_time,
        history,
        beneficiary_id,
    )

    location_change_points, location_change_breakdown = (
        sudden_location_change_risk(
            location_name,
            latitude,
            longitude,
            transaction_time,
            history,
        )
    )

    beneficiary_points, beneficiary_breakdown = (
        beneficiary_risk(
            beneficiary_info,
            history,
            beneficiary_id,
            transaction_time,
        )
    )

    history_points, history_breakdown = history_risk(
        beneficiary_id,
        history,
        location_name,
        device,
    )

    typical_points, typical_breakdown = typical_amount_risk(
        amount,
        beneficiary_id,
        history,
        transaction_category,
    )

    rule_score = min(
        100,
        amount_points
        + time_points
        + frequency_points
        + device_points
        + location_points
        + location_change_points
        + beneficiary_points
        + history_points
        + typical_points,
    )

    breakdown = {
        "amount_risk": amount_points,
        "time_risk": time_points,
        "frequency_risk": frequency_points,
        "device_risk": device_points,
        "location_risk": location_points,
        "location_change_risk": location_change_points,
        "beneficiary_risk": beneficiary_points,
        "history_risk": history_points,
        "typical_amount_risk": typical_points,
    }

    detailed_breakdown = {
        "amount_risk": amount_breakdown,
        "time_risk": time_breakdown,
        "frequency_risk": frequency_breakdown,
        "device_risk": device_breakdown,
        "location_risk": location_breakdown,
        "location_change_risk": location_change_breakdown,
        "beneficiary_risk": beneficiary_breakdown,
        "history_risk": history_breakdown,
        "typical_amount_risk": typical_breakdown,
    }

    features = build_current_ml_features(
        user_id=user_id,
        amount=amount,
        transaction_time=transaction_time,
        category=transaction_category,
        device=device,
        location_name=location_name,
        latitude=latitude,
        longitude=longitude,
        beneficiary_id=beneficiary_id,
        history=history,
        profile=profile,
    )

    ml_probability = predict_ml_risk(
        features
    )

    hybrid_score = combine_risk_scores(rule_score, ml_probability)

    if hybrid_score <= 30:
        risk_level = "LOW"
    elif hybrid_score <= 75:
        risk_level = "ALERT"
    else:
        risk_level = "HIGH"

    factor_names = {
        "amount_risk": "Amount anomaly",
        "time_risk": "Time anomaly",
        "frequency_risk": "Transaction frequency",
        "device_risk": "New/unrecognized device",
        "location_risk": "Unusual location",
        "location_change_risk": "Sudden location change",
        "beneficiary_risk": "Unknown beneficiary",
        "history_risk": "Insufficient transaction history",
        "typical_amount_risk": "Unusual beneficiary amount",
    }

    ranked_factors = sorted(
        [
            {
                "factor": key,
                "label": factor_names[key],
                "points": value,
                "maximum": RISK_WEIGHTS[key],
            }
            for key, value in breakdown.items()
            if value > 0
        ],
        key=lambda item: item["points"],
        reverse=True,
    )

    dominant_factors = [
        item["factor"]
        for item in ranked_factors[:3]
    ]

    if risk_level == "LOW":
        decision = "ALLOW"
        verification_required = False

    elif risk_level == "ALERT":
        decision = "VERIFY"
        verification_required = True

    else:
        decision = "BLOCK"
        verification_required = False

    return {
        "risk_score": hybrid_score,
        "rule_score": rule_score,
        "ml_fraud_probability": ml_probability,
        "risk_level": risk_level,
        "decision": decision,
        "verification_required": verification_required,
        "breakdown": breakdown,
        "detailed_breakdown": detailed_breakdown,
        "ranked_factors": ranked_factors,
        "dominant_risk_factors": dominant_factors,
        "features": features,
        "beneficiary_resolved": beneficiary_info["resolved"],
        "internal_beneficiary_id": beneficiary_id,
        "profile": {
            "transaction_count": profile["transaction_count"],
            "average_amount": round(
                profile["average_amount"],
                2,
            ),
            "median_amount": round(
                profile["median_amount"],
                2,
            ),
            "familiar_locations": profile[
                "familiar_locations"
            ],
            "known_devices": profile[
                "known_devices"
            ],
        },
    }


# ============================================================
# ML FEATURE ENGINE
# ============================================================

def build_current_ml_features(
    user_id: Any,
    amount: float,
    transaction_time: datetime,
    category: Optional[str],
    device: Optional[str],
    location_name: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    beneficiary_id: Any,
    history: list[dict],
    profile: dict,
) -> dict:

    previous = select_previous_transaction(
        history,
        transaction_time,
    )

    hours_since_last = 9999.0

    if previous:

        previous_time = parse_datetime(
            first_value(
                previous,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if previous_time:
            hours_since_last = max(
                0.0,
                (
                    transaction_time
                    - previous_time
                ).total_seconds()
                / 3600,
            )

    count_24h = 0

    for tx in history:

        tx_time = parse_datetime(
            first_value(
                tx,
                "transaction_time",
                "created_at",
                "time",
            )
        )

        if (
            tx_time
            and tx_time < transaction_time
            and transaction_time - tx_time
            <= timedelta(hours=24)
        ):
            count_24h += 1

    average_amount = profile["average_amount"]

    amount_deviation = 0.0

    if average_amount > 0:
        amount_deviation = (
            abs(amount - average_amount)
            / average_amount
        ) * 100

    known_beneficiary = 0

    if beneficiary_id is not None:

        for tx in history:

            tx_beneficiary = first_value(
                tx,
                "beneficiary_id",
                "recipient_id",
            )

            if (
                tx_beneficiary is not None
                and str(tx_beneficiary)
                == str(beneficiary_id)
            ):
                known_beneficiary = 1
                break

    known_devices = profile["known_devices"]

    normalized_device = normalize_text(device)
    new_device = int(
        bool(normalized_device)
        and normalized_device != "unknown-device"
        and normalized_device not in known_devices
    )

    familiar_locations = {
        normalize_text(x)
        for x in profile[
            "familiar_locations"
        ]
    }

    unusual_location = int(
        bool(normalize_text(location_name))
        and normalize_text(location_name) not in familiar_locations
    )

    location_change = 0

    if previous:

        previous_location = normalize_text(
            first_value(
                previous,
                "location_name",
                "location",
                default="",
            )
        )

        if (
            previous_location
            and normalize_text(location_name)
            != previous_location
        ):
            location_change = 1

    unknown_beneficiary = int(
        not known_beneficiary
    )

    no_previous_transaction = int(not history)

    typical_amount = 0

    if beneficiary_id is not None:

        beneficiary_amounts = [
            safe_float(tx.get("amount"))
            for tx in history
            if (
                first_value(
                    tx,
                    "beneficiary_id",
                    "recipient_id",
                )
                is not None
                and str(
                    first_value(
                        tx,
                        "beneficiary_id",
                        "recipient_id",
                    )
                )
                == str(beneficiary_id)
                and safe_float(tx.get("amount")) > 0
            )
        ]

        if beneficiary_amounts:

            avg = statistics.mean(
                beneficiary_amounts
            )

            if avg > 0:
                typical_amount = int(
                    abs(amount - avg) / avg >= 0.5
                )

    time_deviation = 0.0

    if profile["common_hours"]:

        distance = min(
            min(
                abs(
                    transaction_time.hour - hour
                ),
                24
                - abs(
                    transaction_time.hour - hour
                ),
            )
            for hour in profile[
                "common_hours"
            ]
        )

        time_deviation = float(distance)

    night_transaction = int(
        transaction_time.hour < 6
        or transaction_time.hour >= 23
    )

    return {
        "amount_inr": amount,
        "hours_since_last_txn": hours_since_last,
        "transaction_count_24h": count_24h,
        "user_avg_amount_prior": average_amount,
        "amount_deviation_pct": amount_deviation,
        "hour": transaction_time.hour,
        "time_deviation_hours": time_deviation,
        "night_transaction": night_transaction,
        "known_beneficiary": known_beneficiary,
        "new_device": new_device,
        "unusual_location": unusual_location,
        "sudden_location_change": location_change,
        "unknown_beneficiary": unknown_beneficiary,
        "no_previous_transaction": no_previous_transaction,
        "typical_amount_with_beneficiary": typical_amount,
    }


def feature_vector(features: dict) -> list[float]:
    return [
        safe_float(features.get(name))
        for name in FEATURE_NAMES
    ]


def transaction_label(transaction: dict) -> Optional[int]:
    """Return verified/synthetic ground truth, never a payment decision.

    BLOCK/HOLD/ALLOW are outputs of the current risk policy and do not
    establish whether an attempt was actually fraudulent. Training on those
    statuses makes the model learn to imitate the existing rule engine.
    """
    for name in ("is_fraud", "fraud_label"):
        value = transaction.get(name)
        if isinstance(value, bool):
            return int(value)
        if value in (0, 1, "0", "1"):
            return int(value)
    return None


def load_trained_model(path: str) -> dict:
    """Load only a trusted artifact produced by our offline training script.

    Joblib uses pickle internally and must never receive untrusted uploads.
    """
    global ML_CLASSIFIER, ML_ANOMALY, ML_METRICS
    from joblib import load

    artifact = load(path)
    if artifact.get("feature_names") != FEATURE_NAMES:
        raise ValueError("ML model feature schema does not match the backend.")
    if not hasattr(artifact.get("classifier"), "predict_proba"):
        raise ValueError("ML model artifact is missing a supervised classifier.")

    ML_CLASSIFIER = artifact["classifier"]
    ML_ANOMALY = None
    ML_METRICS = {
        **ML_METRICS,
        "trained": True,
        "available": True,
        "source_collection": "offline_csv_training",
        "model_type": "RandomForestClassifier",
        "trained_at": artifact.get("trained_at"),
        "train_rows": artifact.get("train_rows", 0),
        "positive_rows": artifact.get("train_positive_rows", 0),
        "validation_metrics": artifact.get("validation_metrics"),
        "test_metrics": artifact.get("test_metrics"),
        "split_audit": artifact.get("split_audit"),
        "error": None,
    }
    return ML_METRICS


def train_ml_model() -> dict:
    global ML_CLASSIFIER
    global ML_ANOMALY
    global ML_METRICS

    ML_CLASSIFIER = None
    ML_ANOMALY = None

    if not RF_AVAILABLE:
        ML_METRICS.update({
            "available": False,
            "trained": False,
            "error": (
                "scikit-learn is not installed."
            ),
        })
        return ML_METRICS

    try:

        transactions = get_all_historical_transactions(
            ML_MAX_ROWS
        )

        if len(transactions) < 20:
            ML_METRICS.update({
                "available": True,
                "trained": False,
                "rows": len(transactions),
                "error": (
                    "Not enough historical transactions."
                ),
            })
            return ML_METRICS

        # Build per-user history.
        histories = defaultdict(list)

        for tx in transactions:

            user_id = first_value(
                tx,
                "user_id",
            )

            histories[str(user_id)].append(tx)

        X = []
        X_labeled = []
        y_labeled = []

        for tx in transactions:

            user_id = first_value(
                tx,
                "user_id",
            )

            transaction_time = parse_datetime(
                first_value(
                    tx,
                    "transaction_time",
                    "created_at",
                    "time",
                )
            )

            if transaction_time is None:
                continue

            history = histories[
                str(user_id)
            ]

            # Only transactions earlier than the current
            # transaction are used as "prior behaviour".
            prior_history = []

            for previous in history:

                previous_time = parse_datetime(
                    first_value(
                        previous,
                        "transaction_time",
                        "created_at",
                        "time",
                    )
                )

                if (
                    previous_time
                    and previous_time
                    < transaction_time
                ):
                    prior_history.append(
                        previous
                    )

            amounts = [
                safe_float(
                    p.get("amount")
                )
                for p in prior_history
                if safe_float(
                    p.get("amount")
                ) > 0
            ]

            avg_amount = (
                statistics.mean(amounts)
                if amounts
                else safe_float(
                    tx.get("amount")
                )
            )

            familiar_locations = {
                normalize_text(
                    first_value(
                        p,
                        "location_name",
                        "location",
                        default="",
                    )
                )
                for p in prior_history
            }

            known_devices = {
                normalize_text(
                    first_value(
                        p,
                        "sender_device_id",
                        "device_id",
                        "device",
                        default="",
                    )
                )
                for p in prior_history
            }

            beneficiary = first_value(
                tx,
                "beneficiary_id",
                "recipient_id",
            )

            known_beneficiary = int(
                beneficiary is not None
                and any(
                    first_value(
                        p,
                        "beneficiary_id",
                        "recipient_id",
                    )
                    is not None
                    and str(
                        first_value(
                            p,
                            "beneficiary_id",
                            "recipient_id",
                        )
                    )
                    == str(beneficiary)
                    for p in prior_history
                )
            )

            device = first_value(
                tx,
                "sender_device_id",
                "device_id",
                "device",
                default="",
            )

            location = first_value(
                tx,
                "location_name",
                "location",
                default="",
            )

            current_amount = safe_float(
                tx.get("amount")
            )

            amount_deviation = (
                abs(
                    current_amount
                    - avg_amount
                )
                / avg_amount
                if avg_amount > 0
                else 0
            ) * 100

            count_24h = 0

            for p in prior_history:

                p_time = parse_datetime(
                    first_value(
                        p,
                        "transaction_time",
                        "created_at",
                        "time",
                    )
                )

                if (
                    p_time
                    and transaction_time - p_time
                    <= timedelta(hours=24)
                ):
                    count_24h += 1

            previous = None

            if prior_history:
                previous = sorted(
                    prior_history,
                    key=lambda item: (
                        parse_datetime(
                            first_value(
                                item,
                                "transaction_time",
                                "created_at",
                                "time",
                            )
                        )
                        or datetime.min.replace(
                            tzinfo=timezone.utc
                        )
                    ),
                    reverse=True,
                )[0]

            hours_since_last = 9999.0

            if previous:

                previous_time = parse_datetime(
                    first_value(
                        previous,
                        "transaction_time",
                        "created_at",
                        "time",
                    )
                )

                if previous_time:
                    hours_since_last = max(
                        0.0,
                        (
                            transaction_time
                            - previous_time
                        ).total_seconds()
                        / 3600,
                    )

            time_deviation = 0.0

            if prior_history:

                hours = [
                    (
                        parse_datetime(
                            first_value(
                                p,
                                "transaction_time",
                                "created_at",
                                "time",
                            )
                        ).hour
                    )
                    for p in prior_history
                    if parse_datetime(
                        first_value(
                            p,
                            "transaction_time",
                            "created_at",
                            "time",
                        )
                    )
                ]

                if hours:
                    time_deviation = min(
                        min(
                            abs(
                                transaction_time.hour
                                - hour
                            ),
                            24
                            - abs(
                                transaction_time.hour
                                - hour
                            ),
                        )
                        for hour in hours
                    )

            sudden_location_change = 0

            if previous:

                previous_location = normalize_text(
                    first_value(
                        previous,
                        "location_name",
                        "location",
                        default="",
                    )
                )

                if (
                    previous_location
                    and normalize_text(location)
                    != previous_location
                ):
                    sudden_location_change = 1

            typical_amount = 0

            if beneficiary is not None:

                beneficiary_amounts = [
                    safe_float(
                        p.get("amount")
                    )
                    for p in prior_history
                    if (
                        first_value(
                            p,
                            "beneficiary_id",
                            "recipient_id",
                        )
                        is not None
                        and str(
                            first_value(
                                p,
                                "beneficiary_id",
                                "recipient_id",
                            )
                        )
                        == str(beneficiary)
                        and safe_float(
                            p.get("amount")
                        ) > 0
                    )
                ]

                if beneficiary_amounts:

                    beneficiary_avg = (
                        statistics.mean(
                            beneficiary_amounts
                        )
                    )

                    if beneficiary_avg > 0:
                        typical_amount = int(
                            abs(
                                current_amount
                                - beneficiary_avg
                            )
                            / beneficiary_avg
                            >= 0.5
                        )

            features = {
                "amount_inr": current_amount,
                "hours_since_last_txn": hours_since_last,
                "transaction_count_24h": count_24h,
                "user_avg_amount_prior": avg_amount,
                "amount_deviation_pct": amount_deviation,
                "hour": transaction_time.hour,
                "time_deviation_hours": time_deviation,
                "night_transaction": int(
                    transaction_time.hour < 6
                    or transaction_time.hour >= 23
                ),
                "known_beneficiary": known_beneficiary,
                "new_device": int(
                    bool(normalize_text(device))
                    and normalize_text(device) not in known_devices
                ),
                "unusual_location": int(
                    bool(normalize_text(location))
                    and normalize_text(location) not in familiar_locations
                ),
                "sudden_location_change": (
                    sudden_location_change
                ),
                "unknown_beneficiary": int(
                    not known_beneficiary
                ),
                "no_previous_transaction": int(
                    not prior_history
                ),
                "typical_amount_with_beneficiary": (
                    typical_amount
                ),
            }

            vector = feature_vector(features)
            X.append(vector)

            label = transaction_label(tx)
            if label is not None:
                X_labeled.append(vector)
                y_labeled.append(label)

        if len(X) < 20:
            raise RuntimeError(
                "Unable to create enough ML training rows."
            )

        positive_count = sum(y_labeled)

        if positive_count >= 10 and (
            len(y_labeled) - positive_count
        ) >= 10:

            (
                X_train,
                X_test,
                y_train,
                y_test,
            ) = train_test_split(
                X_labeled,
                y_labeled,
                test_size=0.20,
                random_state=42,
                stratify=y_labeled,
            )

            classifier = RandomForestClassifier(
                n_estimators=ML_TREES,
                random_state=42,
                class_weight="balanced",
                min_samples_leaf=2,
                n_jobs=-1,
            )

            classifier.fit(
                X_train,
                y_train,
            )

            predictions = classifier.predict(
                X_test
            )

            probabilities = classifier.predict_proba(
                X_test
            )[:, 1]

            report = classification_report(
                y_test,
                predictions,
                output_dict=True,
                zero_division=0,
            )

            auc = None

            if len(set(y_test)) > 1:
                auc = float(
                    roc_auc_score(
                        y_test,
                        probabilities,
                    )
                )

            ML_CLASSIFIER = classifier

            train_rows = len(X_train)
            test_rows = len(X_test)

            ML_METRICS = {
                "available": True,
                "trained": True,
                "rows": len(X),
                "labeled_rows": len(y_labeled),
                "train_rows": train_rows,
                "test_rows": test_rows,
                "positive_rows": positive_count,
                "fraud_rate": (
                    positive_count / len(y_labeled)
                ),
                "roc_auc": auc,
                "accuracy": float(
                    report["accuracy"]
                ),
                "precision": float(
                    report["1"]["precision"]
                ),
                "recall": float(
                    report["1"]["recall"]
                ),
                "f1": float(
                    report["1"]["f1-score"]
                ),
                "features": FEATURE_NAMES,
                "trained_at": now_utc().isoformat(),
                "error": None,
                "model_type": (
                    "RandomForestClassifier + "
                    "IsolationForest"
                ),
                "source_collection": (
                    "synthetic_transactions"
                ),
            }

        else:

            ML_METRICS = {
                "available": True,
                "trained": False,
                "rows": len(X),
                "labeled_rows": len(y_labeled),
                "train_rows": 0,
                "test_rows": 0,
                "positive_rows": positive_count,
                "fraud_rate": (
                    positive_count / len(y_labeled)
                    if y_labeled else 0.0
                ),
                "roc_auc": None,
                "accuracy": None,
                "precision": None,
                "recall": None,
                "f1": None,
                "features": FEATURE_NAMES,
                "trained_at": None,
                "error": (
                    "Not enough explicit is_fraud/fraud_label "
                    "ground-truth labels for supervised classification."
                ),
                "model_type": (
                    "IsolationForest fallback"
                ),
                "source_collection": (
                    "synthetic_transactions"
                ),
            }

        # Train anomaly model independently.
        if IF_AVAILABLE:

            anomaly = IsolationForest(
                n_estimators=100,
                contamination="auto",
                random_state=42,
            )

            anomaly.fit(X)

            ML_ANOMALY = anomaly

        return ML_METRICS

    except Exception as exc:

        ML_METRICS = {
            "available": RF_AVAILABLE,
            "trained": False,
            "rows": 0,
            "labeled_rows": 0,
            "train_rows": 0,
            "test_rows": 0,
            "positive_rows": 0,
            "fraud_rate": 0.0,
            "roc_auc": None,
            "accuracy": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "features": FEATURE_NAMES,
            "trained_at": None,
            "error": str(exc),
            "model_type": (
                "RandomForestClassifier + "
                "IsolationForest"
            ),
            "source_collection": (
                "synthetic_transactions"
            ),
        }

        print(f"ML training error: {exc}")

        return ML_METRICS


def predict_ml_risk(
    features: dict,
) -> Optional[float]:
    # IsolationForest decision_function is NOT a calibrated fraud probability.
    # Only a supervised classifier with explicit ground-truth labels may
    # supply a probability to the hybrid decision score.
    if ML_CLASSIFIER is None:
        return None
    try:
        probability = float(
            ML_CLASSIFIER.predict_proba([feature_vector(features)])[0][1]
        )
    except Exception:
        return None
    if not math.isfinite(probability):
        return None
    return round(max(0.0, min(100.0, probability * 100)), 2)


# ============================================================
# DECISION / VERIFICATION HELPERS
# ============================================================

def risk_level(score: float) -> str:

    if score <= 30:
        return "LOW"

    if score <= 75:
        return "ALERT"

    return "HIGH"


def decision_for_score(
    score: float,
    verification_success: bool = False,
) -> str:

    if verification_success:

        if score <= 30:
            return "ALLOW"

        if score <= 50:
            return "MONITOR"

        return "HOLD"

    if score <= 30:
        return "ALLOW"

    if score <= 75:
        return "VERIFY"

    return "BLOCK"


def risk_after_verification(
    initial_score: float,
    correct: bool,
    attempt: int,
) -> float:

    if correct:

        # Successful adaptive verification reduces
        # uncertainty but does not blindly erase risk.
        return round(
            max(
                0,
                initial_score - 25,
            ),
            2,
        )

    # Every failed response increases risk.
    return round(
        min(
            100,
            initial_score + 10,
        ),
        2,
    )


# ============================================================
# DYNAMIC QUESTION ENGINE
# ============================================================

RISK_TO_QUESTION_TRIGGERS = {
    "location_risk": [
        "location_risk",
        "unusual_location",
        "location",
    ],
    "location_change_risk": [
        "location_change_risk",
        "location_change",
    ],
    "frequency_risk": [
        "frequency_risk",
        "frequency",
    ],
    "device_risk": [
        "device_risk",
        "device",
    ],
    "beneficiary_risk": [
        "beneficiary_risk",
        "beneficiary",
    ],
    "history_risk": [
        "history_risk",
        "transaction_history",
        "history",
    ],
    "amount_risk": [
        "amount_risk",
        "amount",
    ],
    "typical_amount_risk": [
        "amount_risk",
        "typical_amount",
    ],
    "time_risk": [
        "time_risk",
        "time",
    ],
}


def get_dynamic_questions(
    user_id: Any,
) -> list[dict]:

    collection = get_collection(
        "synthetic_dynamic_questions"
    )

    documents = list(
        collection.find({
            "$or": [
                {"active": True},
                {"active": 1},
                {"active": "1"},
            ]
        })
    )

    questions = []

    for doc in documents:

        options = []

        for key in (
            "option_a",
            "option_b",
            "option_c",
            "option_d",
        ):

            value = doc.get(key)

            if value not in (
                None,
                "",
            ):
                options.append(
                    str(value)
                )

        question_id = first_value(
            doc,
            "question_id",
            "id",
        )

        question_text = first_value(
            doc,
            "question_text",
            "question",
        )

        trigger = first_value(
            doc,
            "trigger_risk_factor",
            "trigger",
            default="general",
        )

        if question_id is None or not question_text:
            continue

        questions.append({
            "question_id": question_id,
            "question_type": first_value(
                doc,
                "question_type",
                default="General",
            ),
            "trigger_risk_factor": trigger,
            "question_text": question_text,
            "options": options,
            "difficulty": safe_int(
                first_value(
                    doc,
                    "difficulty",
                    default=1,
                ),
                1,
            ),
            "tts_text": question_text,
        })

    return questions


def choose_dynamic_question(
    dominant_factors: list[str],
    used_question_ids: list[Any],
) -> Optional[dict]:

    questions = get_dynamic_questions(
        None
    )

    if not questions:
        return None

    used = {
        str(value)
        for value in used_question_ids
    }

    unused = [
        question
        for question in questions
        if str(
            question["question_id"]
        ) not in used
    ]

    if not unused:
        # If all questions were consumed, do not recycle
        # a question during the same session.
        return None

    # First prioritize questions associated with the
    # dominant risk factors.
    prioritized = []

    for factor in dominant_factors:

        triggers = RISK_TO_QUESTION_TRIGGERS.get(
            factor,
            [factor],
        )

        for question in unused:

            trigger = normalize_text(
                question[
                    "trigger_risk_factor"
                ]
            )

            if any(
                normalize_text(t)
                == trigger
                or normalize_text(t)
                in trigger
                or trigger
                in normalize_text(t)
                for t in triggers
            ):
                prioritized.append(
                    question
                )

    # Remove duplicates.
    unique_prioritized = {}

    for question in prioritized:
        unique_prioritized[
            str(question["question_id"])
        ] = question

    prioritized = list(
        unique_prioritized.values()
    )

    pool = (
        prioritized
        if prioritized
        else unused
    )

    return secrets.choice(pool)


def get_question_by_id(
    question_id: Any,
) -> Optional[dict]:

    collection = get_collection(
        "synthetic_dynamic_questions"
    )

    variants = id_variants(
        question_id
    )

    document = collection.find_one({
        "$or": [
            {"question_id": value}
            for value in variants
        ]
    })

    if not document:
        return None

    options = []

    for key in (
        "option_a",
        "option_b",
        "option_c",
        "option_d",
    ):

        value = document.get(key)

        if value not in (
            None,
            "",
        ):
            options.append(
                str(value)
            )

    return {
        "question_id": first_value(
            document,
            "question_id",
            "id",
        ),
        "question_type": first_value(
            document,
            "question_type",
            default="General",
        ),
        "trigger_risk_factor": first_value(
            document,
            "trigger_risk_factor",
            default="general",
        ),
        "question_text": first_value(
            document,
            "question_text",
            "question",
        ),
        "options": options,
        "correct_answer": first_value(
            document,
            "correct_answer",
        ),
        "answer_explanation": first_value(
            document,
            "answer_explanation",
            default="",
        ),
        "difficulty": safe_int(
            first_value(
                document,
                "difficulty",
                default=1,
            ),
            1,
        ),
    }


def judge_answer(
    question: dict,
    answer: str,
) -> bool:

    expected = normalize_text(
        question.get(
            "correct_answer"
        )
    )

    given = normalize_text(
        answer
    )

    if not expected or not given:
        return False

    # Exact match.
    if given == expected:
        return True

    # If speech recognition returns a natural sentence,
    # accept the stored answer if it appears as a phrase.
    if (
        len(expected) >= 4
        and expected in given
    ):
        return True

    # Check whether the answer is an exact option.
    options = question.get(
        "options",
        []
    )

    for option in options:

        if (
            normalize_text(option)
            == given
            and normalize_text(option)
            == expected
        ):
            return True

    # Limited fuzzy matching for speech recognition noise.
    ratio = SequenceMatcher(
        None,
        given,
        expected,
    ).ratio()

    if (
        ratio >= 0.90
        and len(expected) >= 5
    ):
        return True

    return False


# ============================================================
# VERIFICATION SESSION
# ============================================================

def create_verification_session(
    transaction_id: str,
    user_id: Any,
    initial_score: float,
    dominant_factors: list[str],
) -> dict:

    collection = get_collection(
        "verification_sessions"
    )

    first_question = choose_dynamic_question(
        dominant_factors,
        [],
    )

    if not first_question:
        raise HTTPException(
            status_code=500,
            detail=(
                "No active dynamic verification "
                "questions are available."
            ),
        )

    session_id = (
        "VS-"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S"
        )
        + "-"
        + secrets.token_hex(4).upper()
    )

    document = {
        "verification_session_id": session_id,
        "transaction_id": transaction_id,
        "user_id": user_id,
        "initial_risk_score": initial_score,
        "current_risk_score": initial_score,
        "attempts": 0,
        "max_attempts": 3,
        "used_question_ids": [],
        "current_question_id": first_question[
            "question_id"
        ],
        "dominant_risk_factors": dominant_factors,
        "status": "ACTIVE",
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }

    collection.insert_one(
        document
    )

    return {
        "verification_session_id": session_id,
        "question": {
            key: value
            for key, value
            in first_question.items()
            if key
            not in {
                "correct_answer",
                "answer_explanation",
            }
        },
        "attempt": 0,
        "max_attempts": 3,
        "status": "ACTIVE",
    }


# ============================================================
# INCIDENT / ALERT / REPORT
# ============================================================

def create_notification(
    user_id: Any,
    transaction_id: str,
    title: str,
    message: str,
    severity: str,
):
    # Historical synthetic source collections must remain immutable.
    collection_name = "notifications"

    document = {
        "notification_id": (
            "NT-"
            + secrets.token_hex(5).upper()
        ),
        "user_id": user_id,
        "transaction_id": transaction_id,
        "title": title,
        "message": message,
        "severity": severity,
        "read": False,
        "created_at": now_utc(),
    }

    try:
        get_collection(
            collection_name
        ).insert_one(document)
    except Exception as exc:
        print(
            f"Notification error: {exc}"
        )


def create_incident(
    transaction_document: dict,
    reason: str,
) -> str:

    collection = get_collection(
        "incidents"
    )

    incident_id = (
        "INC-"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S"
        )
        + "-"
        + secrets.token_hex(3).upper()
    )

    incident = {
        "incident_id": incident_id,
        "transaction_id": transaction_document[
            "transaction_id"
        ],
        "user_id": transaction_document[
            "user_id"
        ],
        "risk_score": transaction_document.get(
            "risk_score"
        ),
        "risk_level": transaction_document.get(
            "risk_level"
        ),
        "reason": reason,
        "status": "OPEN",
        "created_at": now_utc(),
    }

    collection.insert_one(
        incident
    )

    create_evidence(
        incident_id,
        transaction_document,
    )

    return incident_id


def create_evidence(
    incident_id: str,
    transaction_document: dict,
):
    collection = get_collection(
        "evidence"
    )

    evidence = {
        "evidence_id": (
            "EV-"
            + secrets.token_hex(5).upper()
        ),
        "incident_id": incident_id,
        "transaction_id": transaction_document[
            "transaction_id"
        ],
        "user_id": transaction_document[
            "user_id"
        ],
        "ip_address": transaction_document.get(
            "ip_address"
        ),
        "ip_location": transaction_document.get(
            "ip_location"
        ),
        "device": transaction_document.get(
            "device"
        ),
        "location": transaction_document.get(
            "location_name"
        ),
        "amount": transaction_document.get(
            "amount"
        ),
        "risk_score": transaction_document.get(
            "risk_score"
        ),
        "risk_breakdown": transaction_document.get(
            "risk_breakdown"
        ),
        "created_at": now_utc(),
    }

    collection.insert_one(
        evidence
    )


def create_report(
    transaction_document: dict,
    incident_id: Optional[str],
    destination: str,
) -> str:

    collection = get_collection(
        "reports"
    )

    report_id = (
        "RPT-"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S"
        )
        + "-"
        + secrets.token_hex(3).upper()
    )

    report = {
        "report_id": report_id,
        "incident_id": incident_id,
        "transaction_id": transaction_document[
            "transaction_id"
        ],
        "user_id": transaction_document[
            "user_id"
        ],
        "destination": destination,
        "status": "QUEUED",
        "report_type": (
            "CYBERCRIME"
            if destination == "CYBERCRIME"
            else "POLICE"
        ),
        "transaction_summary": {
            "amount": transaction_document.get(
                "amount"
            ),
            "risk_score": transaction_document.get(
                "risk_score"
            ),
            "risk_level": transaction_document.get(
                "risk_level"
            ),
            "decision": transaction_document.get(
                "decision"
            ),
            "device": transaction_document.get(
                "device"
            ),
            "ip_address": transaction_document.get(
                "ip_address"
            ),
            "location": transaction_document.get(
                "location_name"
            ),
        },
        "created_at": now_utc(),
    }

    collection.insert_one(
        report
    )

    return report_id


# ============================================================
# PYDANTIC API MODELS
# ============================================================

class TransactionRequest(BaseModel):

    user_id: str

    amount: float = Field(
        gt=0,
        allow_inf_nan=False,
    )

    transaction_category: str = "Other"

    transaction_purpose: str = "Other payment"

    recipient_upi_id: Optional[str] = None

    # Internal optional field.
    # The frontend does NOT need to show this.
    beneficiary_id: Optional[str] = None

    device: str = "UNKNOWN-DEVICE"

    ip_address: Optional[str] = None

    ip_location: Optional[str] = None

    location_name: Optional[str] = None

    latitude: Optional[float] = Field(default=None, ge=-90, le=90)

    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    transaction_time: Optional[str] = None

    face_verified: bool = False

    identity_verified: bool = True


class VerificationRequest(BaseModel):

    verification_session_id: str

    answer: str

    # Optional because the backend session is authoritative.
    question_id: Optional[str] = None


class IdentityVerificationRequest(BaseModel):

    user_id: str

    consent: bool

    face_match_score: Optional[float] = None


class ReportSubmitRequest(BaseModel):

    destination: str


# ============================================================
# BASIC ENDPOINTS
# ============================================================

@app.get("/")
def root():
    return {
        "service": "SecureFlow-AI",
        "version": "8.0.0",
        "status": "running",
        "architecture": [
            "User & Identity",
            "Transaction",
            "Context & Intelligence",
            "Feature Engine",
            "Hybrid Risk Engine",
            "Adaptive Verification",
            "Decision Engine",
            "Incident Response",
            "SOC Dashboard",
            "Reporting Queue",
        ],
    }


@app.get("/health")
def health():

    mongodb_status = "DOWN"
    mongodb_error = None

    if mongo_client is not None:

        try:
            mongo_client.admin.command(
                "ping"
            )

            mongodb_status = "OK"

        except Exception as exc:
            # Do not expose Atlas hostnames or connection details publicly.
            mongodb_error = "MongoDB connection unavailable"

    return {
        "service": "SecureFlow-AI",
        "version": "8.0.0",
        "api": "OK",
        "mongodb": mongodb_status,
        "mongodb_error": mongodb_error,
        "ml": (
            "READY"
            if ML_METRICS.get("trained")
            else (
                "AVAILABLE"
                if ML_METRICS.get("available")
                else "UNAVAILABLE"
            )
        ),
        "ml_metrics": ML_METRICS,
    }


# ============================================================
# USERS
# ============================================================

@app.get("/users")
def users():

    try:
        return get_all_users()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/users/{user_id}")
def user_profile(
    user_id: str,
):

    profile = build_user_profile(
        user_id
    )

    return {
        "user": json_safe(
            profile["user"]
        ),
        "behaviour_profile": {
            "transaction_count": profile[
                "transaction_count"
            ],
            "average_amount": profile[
                "average_amount"
            ],
            "median_amount": profile[
                "median_amount"
            ],
            "normal_hours": profile[
                "common_hours"
            ],
            "familiar_locations": profile[
                "familiar_locations"
            ],
            "known_devices": profile[
                "known_devices"
            ],
            "known_beneficiaries": profile[
                "known_beneficiaries"
            ],
            "category_counts": profile[
                "category_counts"
            ],
        },
    }


@app.get("/transactions/{user_id}")
def user_transactions(
    user_id: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
):

    return [
        json_safe(tx)
        for tx in get_user_transactions(
            user_id,
            limit,
            include_all_operational=True,
        )
    ]


# ============================================================
# DYNAMIC QUESTIONS
# ============================================================

@app.get("/dynamic-questions/{user_id}")
def dynamic_questions(
    user_id: str,
):

    questions = get_dynamic_questions(
        user_id
    )

    # Never send correct answers to the frontend.
    return {
        "user_id": user_id,
        "count": len(questions),
        "questions": questions,
    }


@app.get("/dynamic-questions/question/{question_id}")
def dynamic_question(
    question_id: str,
):

    question = get_question_by_id(
        question_id
    )

    if not question:
        raise HTTPException(
            status_code=404,
            detail="Question not found.",
        )

    # Correct answer is NEVER exposed here.
    question.pop(
        "correct_answer",
        None,
    )
    question.pop("answer_explanation", None)

    return question


# ============================================================
# ANALYZE PAYMENT
# ============================================================

@app.post("/analyze-payment")
def analyze_payment(
    request: TransactionRequest,
):

    transaction_time = parse_datetime(
        request.transaction_time
    )

    if transaction_time is None:
        transaction_time = now_utc()

    # Verify user exists.
    user = find_user(
        request.user_id
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail=(
                f"User {request.user_id} "
                "does not exist."
            ),
        )

    result = calculate_risk(
        user_id=request.user_id,
        amount=request.amount,
        transaction_time=transaction_time,
        transaction_category=(
            request.transaction_category
        ),
        transaction_purpose=(
            request.transaction_purpose
        ),
        device=request.device,
        location_name=request.location_name,
        latitude=request.latitude,
        longitude=request.longitude,
        recipient_upi_id=(
            request.recipient_upi_id
        ),
        beneficiary_id=(
            request.beneficiary_id
        ),
    )

    transaction_id = (
        "SF-"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S"
        )
        + "-"
        + secrets.token_hex(3).upper()
    )

    transaction_document = {
        "transaction_id": transaction_id,
        "user_id": (
            safe_int(
                request.user_id,
                0,
            )
            if str(
                request.user_id
            ).isdigit()
            else request.user_id
        ),
        "amount": request.amount,
        "transaction_category": (
            request.transaction_category
        ),
        "transaction_purpose": (
            request.transaction_purpose
        ),
        "recipient_upi_id": (
            request.recipient_upi_id
        ),
        "beneficiary_id": result[
            "internal_beneficiary_id"
        ],
        "device": request.device,
        "ip_address": request.ip_address,
        "ip_location": request.ip_location,
        "location_name": request.location_name,
        "latitude": request.latitude,
        "longitude": request.longitude,
        "transaction_time": transaction_time,
        "risk_score": result["risk_score"],
        "rule_score": result["rule_score"],
        "ml_fraud_probability": result[
            "ml_fraud_probability"
        ],
        "risk_level": result["risk_level"],
        "decision": result["decision"],
        "risk_breakdown": result[
            "breakdown"
        ],
        "detailed_risk_breakdown": result[
            "detailed_breakdown"
        ],
        "dominant_risk_factors": result[
            "dominant_risk_factors"
        ],
        "ranked_factors": result[
            "ranked_factors"
        ],
        "status": (
            "VERIFY"
            if result[
                "verification_required"
            ]
            else result["decision"]
        ),
        "created_at": now_utc(),
    }

    # Store application transactions separately from
    # synthetic historical training data.
    get_collection(
        "transactions"
    ).insert_one(
        transaction_document
    )

    verification = None

    if result["verification_required"]:

        verification = create_verification_session(
            transaction_id=transaction_id,
            user_id=request.user_id,
            initial_score=result[
                "risk_score"
            ],
            dominant_factors=result[
                "dominant_risk_factors"
            ],
        )

    elif result["decision"] == "BLOCK":

        incident_id = create_incident(
            transaction_document,
            "High-risk transaction blocked by hybrid risk engine.",
        )

        create_notification(
            request.user_id,
            transaction_id,
            "Payment blocked",
            (
                "SecureFlow-AI blocked a high-risk "
                "transaction after behavioural analysis."
            ),
            "HIGH",
        )

        report_id = create_report(
            transaction_document,
            incident_id,
            "CYBERCRIME",
        )

        transaction_document[
            "incident_id"
        ] = incident_id

        transaction_document[
            "report_id"
        ] = report_id

        get_collection(
            "transactions"
        ).update_one(
            {
                "transaction_id": transaction_id
            },
            {
                "$set": {
                    "incident_id": incident_id,
                    "report_id": report_id,
                    "status": "BLOCKED",
                }
            },
        )

    else:

        create_notification(
            request.user_id,
            transaction_id,
            "Payment allowed",
            (
                "SecureFlow-AI completed a low-risk "
                "transaction analysis."
            ),
            "LOW",
        )

    return {
        "success": True,
        "transaction_id": transaction_id,
        "user_id": request.user_id,
        "risk_score": result[
            "risk_score"
        ],
        "rule_score": result[
            "rule_score"
        ],
        "ml_fraud_probability": result[
            "ml_fraud_probability"
        ],
        "risk_level": result[
            "risk_level"
        ],
        "decision": result[
            "decision"
        ],
        "verification_required": result[
            "verification_required"
        ],
        "verification": verification,
        "dominant_risk_factors": result[
            "dominant_risk_factors"
        ],
        "ranked_factors": result[
            "ranked_factors"
        ],
        "risk_breakdown": result[
            "breakdown"
        ],
        "detailed_risk_breakdown": result[
            "detailed_breakdown"
        ],
        "behaviour_profile": result[
            "profile"
        ],
        "features": result[
            "features"
        ],
        "message": (
            "Transaction allowed."
            if result["decision"] == "ALLOW"
            else (
                "Additional verification required."
                if result[
                    "verification_required"
                ]
                else "Transaction blocked."
            )
        ),
    }


# ============================================================
# VERIFY CHALLENGE
# ============================================================

@app.post("/verify-challenge")
def verify_challenge(
    request: VerificationRequest,
):

    sessions = get_collection(
        "verification_sessions"
    )

    session = sessions.find_one({
        "verification_session_id":
            request.verification_session_id
    })

    if not session:
        raise HTTPException(
            status_code=404,
            detail="Verification session not found.",
        )

    if session.get("status") != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=(
                "This verification session "
                "is no longer active."
            ),
        )

    current_question_id = session.get(
        "current_question_id"
    )

    # If frontend sends a question ID, it must match
    # the server-controlled question.
    if (
        request.question_id is not None
        and str(request.question_id)
        != str(current_question_id)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Question does not match "
                "verification session."
            ),
        )

    question = get_question_by_id(
        current_question_id
    )

    if not question:
        raise HTTPException(
            status_code=500,
            detail=(
                "The active verification "
                "question no longer exists."
            ),
        )

    correct = judge_answer(
        question,
        request.answer,
    )

    current_attempt = safe_int(
        session.get(
            "attempts",
            0,
        )
    )

    new_attempt = (
        current_attempt + 1
    )

    initial_score = safe_float(
        session.get(
            "initial_risk_score",
            0,
        )
    )

    current_score = safe_float(
        session.get(
            "current_risk_score",
            initial_score,
        )
    )

    if correct:

        updated_score = risk_after_verification(
            current_score,
            True,
            new_attempt,
        )

        updated_level = risk_level(
            updated_score
        )

        updated_decision = decision_for_score(
            updated_score,
            verification_success=True,
        )

        sessions.update_one(
            {
                "_id": session["_id"]
            },
            {
                "$set": {
                    "attempts": new_attempt,
                    "current_risk_score": (
                        updated_score
                    ),
                    "status": "VERIFIED",
                    "verified": True,
                    "completed_at": now_utc(),
                    "updated_at": now_utc(),
                },
                "$addToSet": {
                    "used_question_ids": (
                        current_question_id
                    )
                },
            },
        )

        get_collection(
            "verification_events"
        ).insert_one({
            "verification_session_id":
                request.verification_session_id,
            "transaction_id":
                session["transaction_id"],
            "user_id":
                session["user_id"],
            "question_id":
                current_question_id,
            "attempt":
                new_attempt,
            "correct":
                True,
            "created_at":
                now_utc(),
        })

        get_collection(
            "transactions"
        ).update_one(
            {
                "transaction_id":
                    session["transaction_id"]
            },
            {
                "$set": {
                    "verification_status":
                        "PASSED",
                    "risk_score":
                        updated_score,
                    "risk_level":
                        updated_level,
                    "decision":
                        updated_decision,
                    "status":
                        updated_decision,
                    "updated_at":
                        now_utc(),
                }
            },
        )

        create_notification(
            session["user_id"],
            session["transaction_id"],
            "Verification successful",
            (
                "The verification response was "
                "validated against stored user context."
            ),
            "MEDIUM",
        )

        return {
            "success": True,
            "verification_correct": True,
            "transaction_id": session[
                "transaction_id"
            ],
            "verification_session_id":
                request.verification_session_id,
            "attempt": new_attempt,
            "initial_risk_score":
                initial_score,
            "updated_risk_score":
                updated_score,
            "risk_level":
                updated_level,
            "decision":
                updated_decision,
            "status": "VERIFIED",
            "message": (
                "Verification successful. "
                "The transaction can continue "
                "according to the updated risk."
            ),
        }

    # ========================================================
    # WRONG ANSWER
    # ========================================================

    updated_score = risk_after_verification(
        current_score,
        False,
        new_attempt,
    )

    updated_level = risk_level(
        updated_score
    )

    used_question_ids = list(
        session.get(
            "used_question_ids",
            [],
        )
    )

    if current_question_id not in used_question_ids:
        used_question_ids.append(
            current_question_id
        )

    # Three failed attempts = BLOCK.
    max_attempts = safe_int(
        session.get(
            "max_attempts",
            3,
        ),
        3,
    )

    force_block = (
        new_attempt >= max_attempts
        or updated_score >= 76
    )

    get_collection(
        "verification_events"
    ).insert_one({
        "verification_session_id":
            request.verification_session_id,
        "transaction_id":
            session["transaction_id"],
        "user_id":
            session["user_id"],
        "question_id":
            current_question_id,
        "attempt":
            new_attempt,
        "correct":
            False,
        "created_at":
            now_utc(),
    })

    if force_block:

        sessions.update_one(
            {
                "_id": session["_id"]
            },
            {
                "$set": {
                    "attempts": new_attempt,
                    "current_risk_score":
                        updated_score,
                    "status":
                        "BLOCKED",
                    "verified":
                        False,
                    "completed_at":
                        now_utc(),
                    "updated_at":
                        now_utc(),
                },
                "$addToSet": {
                    "used_question_ids":
                        current_question_id
                },
            },
        )

        transaction = get_collection(
            "transactions"
        ).find_one({
            "transaction_id":
                session["transaction_id"]
        })

        if transaction:

            get_collection(
                "transactions"
            ).update_one(
                {
                    "_id":
                        transaction["_id"]
                },
                {
                    "$set": {
                        "risk_score":
                            updated_score,
                        "risk_level":
                            "HIGH",
                        "decision":
                            "BLOCK",
                        "status":
                            "BLOCKED",
                        "verification_status":
                            "FAILED",
                        "verification_attempts":
                            new_attempt,
                        "updated_at":
                            now_utc(),
                    }
                },
            )

            transaction[
                "risk_score"
            ] = updated_score

            transaction[
                "risk_level"
            ] = "HIGH"

            transaction[
                "decision"
            ] = "BLOCK"

            transaction[
                "status"
            ] = "BLOCKED"

            incident_id = create_incident(
                transaction,
                (
                    "Adaptive verification failed. "
                    "Transaction blocked after "
                    "verification failure."
                ),
            )

            report_id = create_report(
                transaction,
                incident_id,
                "CYBERCRIME",
            )

            get_collection(
                "transactions"
            ).update_one(
                {
                    "transaction_id":
                        session[
                            "transaction_id"
                        ]
                },
                {
                    "$set": {
                        "incident_id":
                            incident_id,
                        "report_id":
                            report_id,
                    }
                },
            )

        create_notification(
            session["user_id"],
            session["transaction_id"],
            "Payment blocked",
            (
                "Verification failed. "
                "SecureFlow-AI blocked the transaction "
                "and created an incident."
            ),
            "HIGH",
        )

        return {
            "success": True,
            "verification_correct": False,
            "transaction_id": session[
                "transaction_id"
            ],
            "verification_session_id":
                request.verification_session_id,
            "attempt": new_attempt,
            "initial_risk_score":
                initial_score,
            "updated_risk_score":
                updated_score,
            "risk_level":
                "HIGH",
            "decision":
                "BLOCK",
            "status":
                "BLOCKED",
            "next_question":
                None,
            "message": (
                "Verification failed. "
                "The transaction has been blocked."
            ),
        }

    # ========================================================
    # WRONG BUT STILL ALLOWED TO CONTINUE VERIFICATION
    # ========================================================

    next_question = choose_dynamic_question(
        session.get(
            "dominant_risk_factors",
            [],
        ),
        used_question_ids,
    )

    if not next_question:

        # Safety fallback: if all questions have been
        # exhausted, block rather than recycle a question.
        sessions.update_one(
            {
                "_id":
                    session["_id"]
            },
            {
                "$set": {
                    "attempts":
                        new_attempt,
                    "current_risk_score":
                        updated_score,
                    "status":
                        "BLOCKED",
                    "verified":
                        False,
                    "updated_at":
                        now_utc(),
                },
                "$setOnInsert": {},
            },
        )

        transaction = get_collection(
            "transactions"
        ).find_one({
            "transaction_id":
                session["transaction_id"]
        })

        if transaction:

            get_collection(
                "transactions"
            ).update_one(
                {
                    "_id":
                        transaction["_id"]
                },
                {
                    "$set": {
                        "risk_score":
                            updated_score,
                        "risk_level":
                            "HIGH",
                        "decision":
                            "BLOCK",
                        "status":
                            "BLOCKED",
                        "verification_status":
                            "FAILED",
                        "updated_at":
                            now_utc(),
                    }
                },
            )

            transaction[
                "risk_score"
            ] = updated_score

            transaction[
                "risk_level"
            ] = "HIGH"

            transaction[
                "decision"
            ] = "BLOCK"

            transaction[
                "status"
            ] = "BLOCKED"

            incident_id = create_incident(
                transaction,
                (
                    "No unused verification "
                    "questions remained."
                ),
            )

            create_report(
                transaction,
                incident_id,
                "CYBERCRIME",
            )

        return {
            "success": True,
            "verification_correct": False,
            "transaction_id":
                session[
                    "transaction_id"
                ],
            "verification_session_id":
                request.verification_session_id,
            "attempt":
                new_attempt,
            "initial_risk_score":
                initial_score,
            "updated_risk_score":
                updated_score,
            "risk_level":
                "HIGH",
            "decision":
                "BLOCK",
            "status":
                "BLOCKED",
            "next_question":
                None,
            "message":
                "No unused verification challenge remained. Transaction blocked.",
        }

    # Save the new question as the ONLY current question.
    sessions.update_one(
        {
            "_id":
                session["_id"]
        },
        {
            "$set": {
                "attempts":
                    new_attempt,
                "current_risk_score":
                    updated_score,
                "current_question_id":
                    next_question[
                        "question_id"
                    ],
                "updated_at":
                    now_utc(),
            },
            "$addToSet": {
                "used_question_ids":
                    current_question_id
            },
        },
    )

    public_question = {
        key: value
        for key, value
        in next_question.items()
        if key
        not in {
            "correct_answer",
            "answer_explanation",
        }
    }

    return {
        "success": True,
        "verification_correct": False,
        "transaction_id": session[
            "transaction_id"
        ],
        "verification_session_id":
            request.verification_session_id,
        "attempt":
            new_attempt,
        "max_attempts":
            max_attempts,
        "initial_risk_score":
            initial_score,
        "updated_risk_score":
            updated_score,
        "risk_level":
            updated_level,
        "decision":
            "VERIFY",
        "status":
            "ACTIVE",
        "next_question":
            public_question,
        "message": (
            "The answer was incorrect. "
            "A different verification question "
            "has been selected."
        ),
    }


# ============================================================
# IDENTITY / FACE VERIFICATION INTERFACE
# ============================================================

@app.post("/verify-identity")
def verify_identity(
    request: IdentityVerificationRequest,
):

    if not request.consent:

        return {
            "success": False,
            "status": "CONSENT_REQUIRED",
            "message": (
                "Consent is required before "
                "biometric verification."
            ),
        }

    # We do NOT pretend that a face was actually verified
    # when there is no biometric model/reference image.
    #
    # The frontend can use this endpoint as the integration
    # point. A real biometric provider can later replace this
    # block.

    # Never trust a score provided by the browser as biometric proof.
    # Keep the endpoint as a future provider integration boundary.
    return {
        "success": False,
        "status": "BIOMETRIC_ENGINE_NOT_CONFIGURED",
        "verified": False,
        "message": (
            "Biometric verification requires a trusted provider; "
            "a client-supplied face score cannot verify identity."
        ),
    }


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.get("/notifications/{user_id}")
def notifications(
    user_id: str,
):
    variants = id_variants(
        user_id
    )

    documents = []
    for name in ("notifications", "synthetic_notifications"):
        if not collection_exists(name):
            continue
        documents.extend(list(get_collection(name).find({
            "$or": [{"user_id": value} for value in variants]
        }).limit(100)))

    documents.sort(
        key=lambda doc: parse_datetime(first_value(
            doc, "created_at", "notification_time", "timestamp"
        )) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return [
        json_safe(doc)
        for doc in documents[:100]
    ]


# ============================================================
# SECURITY EVENTS
# ============================================================

@app.get("/security-events/{user_id}")
def security_events(
    user_id: str,
):

    if not collection_exists(
        "verification_events"
    ):
        return []

    variants = id_variants(
        user_id
    )

    documents = list(
        get_collection(
            "verification_events"
        ).find({
            "$or": [
                {"user_id": value}
                for value in variants
            ]
        })
        .sort(
            "created_at",
            DESCENDING,
        )
        .limit(100)
    )

    return [
        json_safe(doc)
        for doc in documents
    ]


# ============================================================
# INCIDENTS
# ============================================================

@app.get("/incidents")
def incidents(
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
    ),
):

    documents = list(
        get_collection(
            "incidents"
        )
        .find({})
        .sort(
            "created_at",
            DESCENDING,
        )
        .limit(limit)
    )

    return [
        json_safe(doc)
        for doc in documents
    ]


# ============================================================
# REPORTS
# ============================================================

@app.get("/reports")
def reports(
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
    ),
):

    documents = list(
        get_collection(
            "reports"
        )
        .find({})
        .sort(
            "created_at",
            DESCENDING,
        )
        .limit(limit)
    )

    return [
        json_safe(doc)
        for doc in documents
    ]


@app.post("/reports/{report_id}/submit")
def submit_report(
    report_id: str,
    _admin: None = Depends(require_admin_access),
):

    collection = get_collection(
        "reports"
    )

    report = collection.find_one({
        "report_id": report_id
    })

    if not report:
        raise HTTPException(
            status_code=404,
            detail="Report not found.",
        )

    # No real government API is claimed here.
    # This marks the report as ready for an authorized
    # external integration.
    collection.update_one(
        {
            "_id": report["_id"]
        },
        {
            "$set": {
                "status":
                    "QUEUED_FOR_EXTERNAL_INTEGRATION",
                "updated_at":
                    now_utc(),
            }
        },
    )

    return {
        "success": True,
        "report_id": report_id,
        "status":
            "QUEUED_FOR_EXTERNAL_INTEGRATION",
        "destination":
            report.get("destination"),
        "message": (
            "Report is prepared and queued. "
            "A real cybercrime/police API integration "
            "requires the authorized external API."
        ),
    }


# ============================================================
# SOC DASHBOARD
# ============================================================

@app.get("/soc/dashboard")
def soc_dashboard():

    transactions = get_collection(
        "transactions"
    )

    incidents_collection = get_collection(
        "incidents"
    )

    reports_collection = get_collection(
        "reports"
    )

    total_transactions = transactions.count_documents({})

    blocked = transactions.count_documents({
        "decision": "BLOCK"
    })

    verification = transactions.count_documents({
        "decision": "VERIFY"
    })

    allowed = transactions.count_documents({
        "decision": {
            "$in": [
                "ALLOW",
                "MONITOR",
            ]
        }
    })

    open_incidents = incidents_collection.count_documents({
        "status": "OPEN"
    })

    queued_reports = reports_collection.count_documents({
        "status": {
            "$in": [
                "QUEUED",
                "QUEUED_FOR_EXTERNAL_INTEGRATION",
            ]
        }
    })

    recent = list(
        transactions.find({})
        .sort(
            "created_at",
            DESCENDING,
        )
        .limit(10)
    )

    return {
        "summary": {
            "total_transactions":
                total_transactions,
            "allowed_or_monitored":
                allowed,
            "verification_required":
                verification,
            "blocked":
                blocked,
            "open_incidents":
                open_incidents,
            "queued_reports":
                queued_reports,
        },
        "ml": ML_METRICS,
        "recent_transactions": [
            json_safe(tx)
            for tx in recent
        ],
    }


@app.get("/soc/live-feed")
def soc_live_feed():

    documents = list(
        get_collection(
            "transactions"
        )
        .find({})
        .sort(
            "created_at",
            DESCENDING,
        )
        .limit(25)
    )

    return {
        "events": [
            json_safe(doc)
            for doc in documents
        ]
    }


# ============================================================
# DATA READINESS
# ============================================================

@app.get("/admin/data-readiness")
def data_readiness(_admin: None = Depends(require_admin_access)):

    database = require_db()

    collection_names = set(
        database.list_collection_names()
    )

    counts = {}

    for name in sorted(
        collection_names
    ):

        try:
            counts[name] = database[
                name
            ].count_documents({})
        except Exception:
            counts[name] = None

    architecture = {
        "user_identity": (
            collection_exists(
                "synthetic_users"
            )
        ),
        "transaction": (
            collection_exists(
                "synthetic_transactions"
            )
        ),
        "context_intelligence": (
            collection_exists(
                "synthetic_devices"
            )
            or collection_exists(
                "transaction_network_context"
            )
            or collection_exists(
                "transaction_location_history"
            )
        ),
        "feature_engine": True,
        "hybrid_risk_engine": True,
        "adaptive_verification": (
            collection_exists(
                "synthetic_dynamic_questions"
            )
        ),
        "decision_engine": True,
        "incident_response": True,
        "soc_dashboard": True,
        "report_queue": True,
    }

    return {
        "mongodb": "OK",
        "database": MONGODB_DB,
        "collections": counts,
        "architecture": architecture,
        "excluded_from_decision": sorted(
            EXCLUDED_FROM_RISK_SOURCE
        ),
        "risk_weights_total": sum(
            RISK_WEIGHTS.values()
        ),
        "risk_weights": RISK_WEIGHTS,
        "ml": ML_METRICS,
    }


# ============================================================
# ML TRAINING ENDPOINT
# ============================================================

@app.post("/admin/train-ml")
def retrain_ml(_admin: None = Depends(require_admin_access)):

    metrics = train_ml_model()

    return {
        "success": bool(
            metrics.get("trained")
        ),
        "ml_metrics": metrics,
    }


# ============================================================
# RISK WEIGHTS
# ============================================================

@app.get("/risk-weights")
def risk_weights():

    return {
        "total": sum(
            RISK_WEIGHTS.values()
        ),
        "weights": RISK_WEIGHTS,
        "bands": {
            "LOW": "0-30",
            "ALERT": "31-75",
            "HIGH": "76-100",
        },
    }


# ============================================================
# TRANSACTION DETAILS
# ============================================================

@app.get("/transactions/detail/{transaction_id}")
def transaction_detail(
    transaction_id: str,
):

    transaction = get_collection(
        "transactions"
    ).find_one({
        "transaction_id":
            transaction_id
    })

    if not transaction:

        transaction = get_collection(
            "synthetic_transactions"
        ).find_one({
            "transaction_id":
                transaction_id
        })

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found.",
        )

    return json_safe(
        transaction
    )


# ============================================================
# ADMIN RESET - DEMO ONLY
# ============================================================

@app.post("/admin/reset-dashboard")
def reset_dashboard(
    x_confirm_demo_reset: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_access),
):
    # This endpoint is intentionally disabled on any production deployment.
    if APP_ENV != "development" or not ENABLE_DEMO_RESET:
        raise HTTPException(status_code=403, detail="Demo reset is disabled.")
    if x_confirm_demo_reset != "reset-operational-data":
        raise HTTPException(status_code=400, detail="Reset confirmation is required.")

    collections = [
        "transactions",
        "notifications",
        "verification_sessions",
        "verification_events",
        "incidents",
        "evidence",
        "reports",
        "audit_logs",
    ]

    deleted = {}

    for name in collections:

        if collection_exists(name):

            result = get_collection(
                name
            ).delete_many({})

            deleted[name] = (
                result.deleted_count
            )

    return {
        "success": True,
        "deleted": deleted,
        "message": (
            "Operational demo data reset. "
            "Synthetic source data was not modified."
        ),
    }


# ============================================================
# OPTIONAL LEGACY-COMPATIBILITY ENDPOINT
# ============================================================

@app.get("/verify-challenge/{session_id}")
def get_verification_session(
    session_id: str,
):

    session = get_collection(
        "verification_sessions"
    ).find_one({
        "verification_session_id":
            session_id
    })

    if not session:
        raise HTTPException(
            status_code=404,
            detail="Verification session not found.",
        )

    question = get_question_by_id(
        session.get(
            "current_question_id"
        )
    )

    if question:
        question.pop(
            "correct_answer",
            None,
        )
        question.pop("answer_explanation", None)

    return {
        "verification_session_id":
            session.get(
                "verification_session_id"
            ),
        "transaction_id":
            session.get(
                "transaction_id"
            ),
        "user_id":
            session.get(
                "user_id"
            ),
        "attempts":
            session.get(
                "attempts",
                0,
            ),
        "max_attempts":
            session.get(
                "max_attempts",
                3,
            ),
        "risk_score":
            session.get(
                "current_risk_score"
            ),
        "status":
            session.get(
                "status"
            ),
        "current_question":
            question,
    }


# ============================================================
# ERROR HANDLING
# ============================================================

@app.exception_handler(PyMongoError)
async def mongo_exception_handler(
    request,
    exc,
):
    # Always return a valid Response without leaking connection details.
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "error": "Database operation unavailable",
        },
    )
