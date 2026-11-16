"""End-to-end payment route smoke test with an in-memory MongoDB substitute."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock
from fastapi.testclient import TestClient

import main


class PaymentFlowTests(unittest.TestCase):
    def setUp(self):
        self.database = mongomock.MongoClient()["isolated_demo_test"]
        self.database["synthetic_users"].insert_one({"user_id": 1, "full_name": "Synthetic Test User"})
        start = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.database["synthetic_transactions"].insert_many([
            {
                "transaction_id": f"historical-{index}",
                "user_id": 1,
                "amount": 100,
                "transaction_time": start + timedelta(days=index),
                "sender_device_id": "trusted-device",
                "beneficiary_id": 3,
                "location_name": "Haldia",
                "status": "ALLOWED",
            }
            for index in range(10)
        ])
        self.database["synthetic_devices"].insert_one({
            "device_id": "trusted-device", "user_id": 1, "is_trusted": True
        })
        self.database["synthetic_dynamic_questions"].insert_one({
            "question_id": 1,
            "question_text": "Confirm this synthetic payment.",
            "option_a": "Yes", "option_b": "No",
            "correct_answer": "Yes", "active": 1,
            "trigger_risk_factor": "amount_risk",
        })
        self.client = TestClient(main.app)
        self.patched_db = patch.object(main, "db", self.database)
        self.patched_classifier = patch.object(main, "ML_CLASSIFIER", None)
        self.patched_db.start()
        self.patched_classifier.start()

    def tearDown(self):
        self.patched_classifier.stop()
        self.patched_db.stop()
        self.client.close()

    def test_simulated_payment_is_scored_and_operationally_saved(self):
        response = self.client.post("/analyze-payment", json={
            "user_id": "1",
            "amount": 20000,
            "recipient_upi_id": "new-beneficiary@demo",
            "device": "unrecognized-device",
            "location_name": "Unfamiliar City",
            "transaction_time": "2026-11-16T01:00:00+00:00",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertGreaterEqual(result["rule_score"], 0)
        self.assertLessEqual(result["risk_score"], 100)
        self.assertIn(result["decision"], {"ALLOW", "VERIFY", "BLOCK"})
        self.assertEqual(self.database["transactions"].count_documents({}), 1)
        self.assertEqual(self.database["synthetic_transactions"].count_documents({}), 10)

        if result["decision"] == "BLOCK":
            self.assertEqual(self.database["incidents"].count_documents({}), 1)
            self.assertEqual(self.database["reports"].count_documents({}), 1)
            self.assertEqual(self.database["notifications"].count_documents({}), 1)
            self.assertEqual(self.database["reports"].find_one()["status"], "QUEUED")
        elif result["decision"] == "VERIFY":
            self.assertEqual(self.database["verification_sessions"].count_documents({}), 1)
            self.assertNotIn("correct_answer", result["verification"]["question"])


if __name__ == "__main__":
    unittest.main()
