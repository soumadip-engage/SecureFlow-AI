"""Read-only audit unit tests; no Atlas credentials or live MongoDB needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_mongodb import inspect_collection, summarize_database


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs
    def limit(self, count):
        return self.docs[:count]


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs
    def estimated_document_count(self):
        return len(self.docs)
    def find(self, query, projection):
        assert query == {}
        assert projection.get("_id") == 0
        return FakeCursor([
            {key: value for key, value in doc.items() if projection.get(key)}
            for doc in self.docs
        ])


class FakeDatabase:
    name = "SecureFlowAI_DevTest"
    def __init__(self, collections):
        self.collections = {name: FakeCollection(docs) for name, docs in collections.items()}
    def list_collection_names(self):
        return list(self.collections)
    def __getitem__(self, name):
        return self.collections[name]


class AuditTests(unittest.TestCase):
    def test_audit_never_emits_sensitive_values(self):
        db = FakeDatabase({"synthetic_users": [{
            "user_id": "user-private-123", "email": "private@example.com",
            "phone": "+91-9000000000", "name": "Private Person",
        }]})
        output = str(summarize_database(db))
        for secret in ("user-private-123", "private@example.com", "+91-9000000000", "Private Person"):
            self.assertNotIn(secret, output)
        self.assertIn("synthetic_users", output)
        self.assertIn("email", output)

    def test_missing_collections_and_sample_presence(self):
        db = FakeDatabase({"synthetic_transactions": [
            {"transaction_id": "t1", "is_fraud": 1, "amount": 150},
            {"transaction_id": "t2", "amount": 200},
        ]})
        result = summarize_database(db, sample_size=1)
        tx = result["collections"]["synthetic_transactions"]
        self.assertEqual(tx["sampled"], 1)
        self.assertEqual(tx["key_presence"]["is_fraud"]["present"], 1)
        self.assertFalse(result["collections"]["synthetic_users"]["exists"])

    def test_collection_does_not_mutate(self):
        c = FakeCollection([{"user_id": "u1"}])
        self.assertEqual(inspect_collection(c, 10, ("user_id",))["sampled"], 1)


if __name__ == "__main__":
    unittest.main()
