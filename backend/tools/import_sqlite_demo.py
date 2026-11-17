"""Import an inspected SQLite demo database into *only* SecureFlowAI_DevTest.

Dry-run by default. No collection drops, deletes, replacement of existing
documents, or changes to the SQLite source. Each source table maps to a
synthetic_* collection; operational records remain separate.

Example, from backend/:
  .\\.venv\\Scripts\\python.exe tools/import_sqlite_demo.py --source "C:\\path\\to\\Database of 6000 trans.db"
  .\\.venv\\Scripts\\python.exe tools/import_sqlite_demo.py --source "C:\\path\\to\\Database of 6000 trans.db" --apply --confirm-db SecureFlowAI_DevTest
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

ALLOWED_DATABASE = "SecureFlowAI_DevTest"

# Keep source/reference and operational/demo collections separated.
TABLE_IDS = {
    "users": "user_id",
    "devices": "device_id",
    "locations": "location_id",
    "beneficiaries": "beneficiary_id",
    "beneficiary_devices": "beneficiary_device_id",
    "transactions": "transaction_id",
    "risk_scores": "risk_id",
    "dynamic_questions": "question_id",
    "verification_events": "verification_id",
    "incidents": "incident_id",
    "notifications": "notification_id",
    "user_beneficiary_relationships": "relationship_id",
    "transaction_location_history": "history_id",
    "transaction_network_context": "network_context_id",
    "device_context_history": "device_event_id",
    "dataset_metadata": "key",
}

def target_name(source_table: str) -> str:
    return "synthetic_" + source_table

def inspect_source(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """Ensure primary IDs really exist; abort before any write on mismatch."""
    names = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "users" not in names or "transactions" not in names:
        raise ValueError("Missing core source tables: users and transactions required")
    result = []
    for name, identifier in TABLE_IDS.items():
        if name not in names:
            continue
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')}
        if identifier not in cols:
            # Some detail tables are keyed by different IDs across exports.
            # Do not silently make up keys or copy these records as duplicates.
            raise ValueError(f"Table {name} has no expected key {identifier}; inspect schema before importing")
        count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        if conn.execute(f'SELECT COUNT(*) FROM "{name}" WHERE "{identifier}" IS NULL').fetchone()[0]:
            raise ValueError(f"Null key in {name}.{identifier}")
        if conn.execute(f'SELECT COUNT(DISTINCT "{identifier}") FROM "{name}"').fetchone()[0] != count:
            raise ValueError(f"Duplicate key in {name}.{identifier}; manual mapping needed")
        result.append((name, identifier, count))
    return result

def main() -> None:
    parser = argparse.ArgumentParser(description="Safe SQLite synthetic demo importer")
    parser.add_argument("--source", required=True, type=Path, help="SQLite .db file")
    parser.add_argument("--apply", action="store_true", help="Perform inserts (default: read-only dry run)")
    parser.add_argument("--confirm-db", default="", help=f"Required with --apply: {ALLOWED_DATABASE}")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    configured_db = os.getenv("MONGODB_DB", "").strip()
    if configured_db != ALLOWED_DATABASE:
        parser.error(f"Refusing to run: MONGODB_DB must equal {ALLOWED_DATABASE}")
    if args.apply and args.confirm_db != ALLOWED_DATABASE:
        parser.error(f"Writing requires --confirm-db {ALLOWED_DATABASE}")
    if not args.source.is_file():
        parser.error("Source SQLite database file not found")

    uri = args.source.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            plan = inspect_source(conn)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Mode: {'INSERT MISSING ONLY' if args.apply else 'DRY RUN — NO WRITES'}")
        print(f"Destination: {configured_db}")
        for name, _, count in plan:
            print(f"{name} -> {target_name(name)}: {count} source records")
        print(f"Validated {len(plan)} tables and {sum(n for _, _, n in plan)} records.")
        if not args.apply:
            print("No MongoDB connection or database writes were made.")
            return

        connection_string = os.getenv("MONGODB_URI", "").strip()
        if not connection_string or connection_string.startswith("YOUR_") or "REPLACE_WITH" in connection_string:
            parser.error("MONGODB_URI is missing from backend/.env")

        client = MongoClient(connection_string, serverSelectionTimeoutMS=10000)
        try:
            client.admin.command("ping")
            db = client[ALLOWED_DATABASE]
            for table, key, _ in plan:
                collection = db[target_name(table)]
                # Unique source IDs make reruns idempotent, including under retries.
                collection.create_index([(key, 1)], unique=True)
                inserted = 0
                for chunk in chunks(conn, table, size=300):
                    operations = [
                        UpdateOne(
                            {key: row[key]},
                            {"$setOnInsert": dict(row)},
                            upsert=True,
                        )
                        for row in chunk
                    ]
                    result = collection.bulk_write(operations, ordered=True)
                    inserted += result.upserted_count
                print(f"{target_name(table)}: {inserted} new documents (existing IDs kept)")
            print("Import complete. Re-run the read-only Atlas audit to verify counts.")
        except Exception as exc:
            # Don't print driver exceptions which may contain credential-bearing URLs.
            raise SystemExit(f"Import stopped: {type(exc).__name__}. Check Atlas access and field-key compatibility.") from None
        finally:
            client.close()


def chunks(conn: sqlite3.Connection, table: str, size: int):
    cursor = conn.execute(f'SELECT * FROM "{table}"')
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        yield rows


if __name__ == "__main__":
    main()
