"""SQLite importer validation tests. No Atlas connection or writes."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from import_sqlite_demo import TABLE_IDS, inspect_source, target_name


class ImporterTests(unittest.TestCase):
    def make_db(self, *, duplicate=False, omit_core=False):
        conn = sqlite3.connect(":memory:")
        if not omit_core:
            conn.execute("CREATE TABLE users (user_id INTEGER, full_name TEXT)")
            conn.execute("INSERT INTO users VALUES (1, 'Demo User')")
        conn.execute("CREATE TABLE transactions (transaction_id TEXT, amount REAL)")
        conn.execute("INSERT INTO transactions VALUES ('T-01', 100)")
        if duplicate:
            conn.execute("INSERT INTO transactions VALUES ('T-01', 200)")
        return conn

    def test_valid_source_and_names(self):
        conn = self.make_db()
        plan = inspect_source(conn)
        self.assertEqual({x[0]: x[2] for x in plan},
                         {"users": 1, "transactions": 1})
        self.assertEqual(target_name("users"), "synthetic_users")
        conn.close()

    def test_duplicate_identifier_fails_before_import(self):
        conn = self.make_db(duplicate=True)
        with self.assertRaisesRegex(ValueError, "Duplicate key"):
            inspect_source(conn)
        conn.close()

    def test_missing_users_fails(self):
        conn = self.make_db(omit_core=True)
        with self.assertRaisesRegex(ValueError, "Missing core source tables"):
            inspect_source(conn)
        conn.close()

    def test_source_ids_match_checked_sqlite_schema(self):
        expected = {
            "verification_events": "verification_id",
            "transaction_location_history": "history_id",
            "transaction_network_context": "network_context_id",
            "device_context_history": "device_event_id",
            "dataset_metadata": "key",
        }
        for table, id_field in expected.items():
            self.assertEqual(TABLE_IDS[table], id_field)


if __name__ == "__main__":
    unittest.main()
