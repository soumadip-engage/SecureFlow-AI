"""Train an offline model on the team's labeled synthetic CSV.

Run from backend/:
  python -m ml.train_from_csv --csv /path/to/secureflow_ai_1m_ml_training.csv

Only ground-truth ``is_fraud`` is used as the label. By default, users are
assigned to disjoint train/validation/test sets via a stable hash of user_id.
Synthetic risk scores, decisions and fraud-pattern names are NOT model inputs.
User-ID overlap between holdout splits is reported, not hidden.
"""

import argparse
import csv
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from joblib import dump
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from main import FEATURE_NAMES


def reservoir_add(items, seen, cap, item, rng):
    """Bound training memory without discarding the entire majority class."""
    if len(items) < cap:
        items.append(item)
    else:
        idx = rng.randrange(seen)
        if idx < cap:
            items[idx] = item


def build_split_audit(users_by_split):
    train = users_by_split["train"]
    validation = users_by_split["validation"]
    test = users_by_split["test"]
    overlap_validation = len(train & validation)
    overlap_test = len(train & test)
    return {
        "unique_users": {split: len(users) for split, users in users_by_split.items()},
        "train_validation_user_overlap": overlap_validation,
        "train_test_user_overlap": overlap_test,
        "validation_test_user_overlap": len(validation & test),
        "caveat": (
            "The supplied holdout splits reuse user IDs across training and evaluation. "
            "Metrics may be optimistic for unseen users; use a user-disjoint "
            "or genuinely prospective holdout before claiming generalization."
            if overlap_validation or overlap_test
            else "Training and holdout user IDs are disjoint."
        ),
    }


def user_disjoint_split(user_id, seed):
    """A user belongs to one split regardless of source CSV row ordering."""
    digest = hashlib.sha256(f"{seed}:{user_id}".encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / (1 << 64)
    if fraction < 0.70:
        return "train"
    if fraction < 0.85:
        return "validation"
    return "test"


def read_dataset(path, max_normal, max_fraud, seed, split_strategy="user-disjoint"):
    if split_strategy not in {"provided", "user-disjoint"}:
        raise ValueError("split_strategy must be provided or user-disjoint")
    rng = random.Random(seed)
    sampled = {0: [], 1: []}
    seen = {0: 0, 1: 0}
    evaluations = {"validation": ([], []), "test": ([], [])}
    counts = {"train": 0, "validation": 0, "test": 0}
    users_by_split = {"train": set(), "validation": set(), "test": set()}

    with open(path, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = set(FEATURE_NAMES) | {"is_fraud", "user_id"}
        if split_strategy == "provided":
            required.add("dataset_split")
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"CSV is missing ML columns: {', '.join(missing)}")

        for row in reader:
            user_id = (row["user_id"] or "").strip()
            if not user_id:
                raise ValueError("Every row must have a user_id for split integrity checks.")
            split = (
                user_disjoint_split(user_id, seed)
                if split_strategy == "user-disjoint"
                else (row["dataset_split"] or "").lower().strip()
            )
            label_text = (row["is_fraud"] or "").strip()
            if split not in counts or label_text not in {"0", "1"}:
                raise ValueError("Each row must have a recognized split and binary is_fraud label.")
            label = int(label_text)
            users_by_split[split].add(user_id)
            try:
                features = [float(row[name]) for name in FEATURE_NAMES]
            except (ValueError, TypeError) as exc:
                raise ValueError("CSV contains a missing/non-numeric model feature.") from exc

            counts[split] += 1
            if split == "train":
                seen[label] += 1
                cap = max_fraud if label else max_normal
                reservoir_add(sampled[label], seen[label], cap, (features, label), rng)
            else:
                x, y = evaluations[split]
                x.append(features)
                y.append(label)

    train_data = sampled[0] + sampled[1]
    rng.shuffle(train_data)
    if len(sampled[0]) < 10 or len(sampled[1]) < 10:
        raise ValueError("Training requires at least 10 examples from each label.")
    for split, (x, y) in evaluations.items():
        if len(x) < 1 or len(set(y)) != 2:
            raise ValueError(f"{split} requires both normal and fraud examples.")
    audit = build_split_audit(users_by_split)
    audit["strategy"] = split_strategy
    return train_data, evaluations, counts, audit


def evaluate(classifier, x, y):
    predicted = classifier.predict(x)
    probabilities = classifier.predict_proba(x)[:, 1]
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    return {
        "total": len(y),
        "fraud": int(sum(y)),
        "precision": float(precision_score(y, predicted, zero_division=0)),
        "recall": float(recall_score(y, predicted, zero_division=0)),
        "f1": float(f1_score(y, predicted, zero_division=0)),
        "average_precision": float(average_precision_score(y, probabilities)),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "true_negative": int(tn),
        "false_positive": int(fp),
        "false_negative": int(fn),
        "true_positive": int(tp),
    }


def train(path, output, max_normal=50000, max_fraud=10000, trees=120, seed=42,
          split_strategy="user-disjoint"):
    if max_normal < 10 or max_fraud < 10 or trees < 10:
        raise ValueError("Sampling caps and tree count must each be at least 10.")
    train_data, evaluations, counts, split_audit = read_dataset(
        path, max_normal, max_fraud, seed, split_strategy
    )
    x_train = [pair[0] for pair in train_data]
    y_train = [pair[1] for pair in train_data]

    classifier = RandomForestClassifier(
        n_estimators=trees,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=seed,
    )
    classifier.fit(x_train, y_train)

    validation = evaluate(classifier, *evaluations["validation"])
    test = evaluate(classifier, *evaluations["test"])
    artifact = {
        "classifier": classifier,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_rows": len(train_data),
        "train_positive_rows": sum(y_train),
        "source_counts": counts,
        "split_audit": split_audit,
        "validation_metrics": validation,
        "test_metrics": test,
    }
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    dump(artifact, destination)
    return {k: v for k, v in artifact.items() if k != "classifier"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to labeled CSV")
    parser.add_argument("--output", default="models/secureflow_rf.joblib")
    parser.add_argument("--max-normal", type=int, default=50000)
    parser.add_argument("--max-fraud", type=int, default=10000)
    parser.add_argument("--trees", type=int, default=120)
    parser.add_argument(
        "--split-strategy", choices=["user-disjoint", "provided"],
        default="user-disjoint",
        help="Default holds out entire users; 'provided' reuses the CSV's split labels.",
    )
    args = parser.parse_args()
    metrics = train(
        args.csv, args.output, args.max_normal, args.max_fraud, args.trees,
        split_strategy=args.split_strategy,
    )
    print(json.dumps(metrics, indent=2))
