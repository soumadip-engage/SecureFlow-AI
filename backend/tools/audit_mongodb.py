"""Read-only, privacy-safe schema audit for a SecureFlow MongoDB database.

Does not read or print document values, call insert/update/delete, or create indexes.
Run locally with: .\.venv\Scripts\python.exe tools/audit_mongodb.py
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

EXPECTED = (
    "synthetic_users",
    "synthetic_transactions",
    "synthetic_devices",
    "synthetic_beneficiaries",
    "synthetic_locations",
    "synthetic_dynamic_questions",
    "transactions",
    "verification_sessions",
    "verification_events",
    "incidents",
    "notifications",
    "reports",
)
# Field presence is inspected; actual emails, numbers, faces, transaction
# amounts, UPI IDs, IPs, and other stored values never enter the report.
PROFILE_KEYS = ("user_id", "email", "phone", "mobile", "name", "created_at")
PAYMENT_KEYS = (
    "transaction_id", "user_id", "amount", "transaction_time",
    "is_fraud", "fraud_label", "recipient_upi_id", "device",
    "location_name", "decision", "status",
)


def inspect_collection(collection, sample_size: int, keys: tuple[str, ...]) -> dict:
    """Summarize collection shape without returning any document values."""
    result = {"estimated_count": collection.estimated_document_count(),
              "sampled": 0, "top_level_fields": [], "key_presence": {}}
    fields: Counter[str] = Counter()
    present: Counter[str] = Counter()
    # Projection limits fetched values to existence flags: MongoDB will return
    # field values for included keys, but these are used only in-memory for
    # membership checks and are never printed or serialized.
    projection = {"_id": 0, **{key: 1 for key in keys}}
    for document in collection.find({}, projection).limit(sample_size):
        result["sampled"] += 1
        for key in document:
            fields[key] += 1
        for key in keys:
            if key in document and document[key] is not None:
                present[key] += 1
    result["top_level_fields"] = sorted(fields)
    result["key_presence"] = {
        key: {"present": present[key], "sampled": result["sampled"]}
        for key in keys
    }
    return result


def summarize_database(database, sample_size: int = 25) -> dict:
    names = set(database.list_collection_names())
    summaries: dict = {}
    for name in EXPECTED:
        if name not in names:
            summaries[name] = {"exists": False}
            continue
        keys = PROFILE_KEYS if name.endswith("_users") else PAYMENT_KEYS if name.endswith("_transactions") or name == "transactions" else ("user_id", "created_at")
        try:
            summaries[name] = {"exists": True, **inspect_collection(database[name], sample_size, keys)}
        except PyMongoError:
            summaries[name] = {"exists": True, "error": "Cannot inspect with current database permissions"}
    return {
        "database": database.name,
        "audit_type": "read_only_schema_presence",
        "sample_limit_per_collection": sample_size,
        "collections": summaries,
        "unrecognized_collection_count": len(names - set(EXPECTED)),
        "note": "Counts are approximate; sampled field presence does not establish record validity. No values are shown.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Privacy-safe, read-only MongoDB structure check")
    parser.add_argument("--sample-size", type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 100:
        parser.error("--sample-size must be from 1 to 100")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    uri = os.getenv("MONGODB_URI", "").strip()
    db_name = os.getenv("MONGODB_DB", "SecureFlowDB").strip()
    if not uri or uri.startswith("YOUR_") or "REPLACE_WITH" in uri:
        parser.error("Set MONGODB_URI in backend/.env first (do not share it)")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
        report = summarize_database(client[db_name], args.sample_size)
        print(json.dumps(report, indent=2))
    except PyMongoError as error:
        # Do not echo the exception: driver exceptions can contain Atlas hostnames.
        raise SystemExit(f"Audit failed: {type(error).__name__}. Check Atlas connectivity and read permissions.") from None
    finally:
        client.close()


if __name__ == "__main__":
    main()
