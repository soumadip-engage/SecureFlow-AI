"""MongoDB-free regression checks. Run from backend: python -m unittest discover -s tests -v."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

import main


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_reset_requires_admin_configuration(self):
        with patch.object(main, "ADMIN_API_KEY", ""):
            response = self.client.post("/admin/reset-dashboard")
        self.assertEqual(response.status_code, 503)

    def test_public_deployment_fails_closed_until_user_auth_exists(self):
        with patch.object(main, "APP_ENV", "production"):
            self.assertEqual(self.client.get("/users").status_code, 503)
            self.assertEqual(self.client.get("/health").status_code, 200)

    def test_admin_key_is_not_optional(self):
        with patch.object(main, "ADMIN_API_KEY", "a-private-test-key"):
            self.assertEqual(self.client.post("/admin/train-ml").status_code, 403)
            self.assertEqual(
                self.client.get("/admin/data-readiness", headers={"X-Admin-Key": "wrong"}).status_code,
                403,
            )

    def test_reset_stays_disabled_even_with_admin_key(self):
        with (
            patch.object(main, "ADMIN_API_KEY", "a-private-test-key"),
            patch.object(main, "APP_ENV", "development"),
            patch.object(main, "ENABLE_DEMO_RESET", False),
            patch.object(main, "get_collection", side_effect=AssertionError("No writes allowed")),
        ):
            response = self.client.post(
                "/admin/reset-dashboard",
                headers={"X-Admin-Key": "a-private-test-key", "X-Confirm-Demo-Reset": "reset-operational-data"},
            )
        self.assertEqual(response.status_code, 403)

    def test_biometric_score_from_browser_cannot_verify_identity(self):
        response = self.client.post(
            "/verify-identity",
            json={"user_id": "demo", "consent": True, "face_match_score": 1.0},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["verified"])
        self.assertEqual(response.json()["status"], "BIOMETRIC_ENGINE_NOT_CONFIGURED")

    def test_transaction_input_rejects_invalid_numbers(self):
        request = {"user_id": "demo", "amount": -1}
        self.assertEqual(self.client.post("/analyze-payment", json=request).status_code, 422)
        request["amount"] = 100
        request["latitude"] = 1000
        self.assertEqual(self.client.post("/analyze-payment", json=request).status_code, 422)

    def test_mongo_error_response_does_not_leak_connection_details(self):
        result = asyncio.run(main.mongo_exception_handler(None, PyMongoError("mongodb+srv://secret@host")))
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(b"secret", result.body)


class RiskAndModelTests(unittest.TestCase):
    def test_risk_weights_are_bounded_to_100(self):
        self.assertEqual(sum(main.RISK_WEIGHTS.values()), 100)

    def test_missing_device_and_location_are_not_new_signals(self):
        profile = {"known_devices": [], "familiar_locations": [], "average_amount": 1000}
        dt = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        device_score, device_factors = main.device_risk("UNKNOWN-DEVICE", profile, dt, 8000, True)
        location_score, location_factors = main.location_risk(None, profile, 8000, 15)
        self.assertEqual((device_score, location_score), (0, 0))
        self.assertFalse(device_factors["signal_available"])
        self.assertFalse(location_factors["signal_available"])

    def test_rule_score_cannot_be_downgraded_by_model(self):
        self.assertEqual(main.combine_risk_scores(90, 0), 90)
        self.assertEqual(main.combine_risk_scores(20, None), 20)
        self.assertEqual(main.combine_risk_scores(20, 100), 40)

    def test_status_is_not_fraud_ground_truth(self):
        self.assertIsNone(main.transaction_label({"status": "BLOCKED"}))
        self.assertIsNone(main.transaction_label({"status": "ALLOWED"}))
        self.assertEqual(main.transaction_label({"status": "ALLOWED", "is_fraud": True}), 1)
        self.assertEqual(main.transaction_label({"fraud_label": "0"}), 0)

    def test_ml_features_never_read_synthetic_decisions_or_labels(self):
        prohibited = {"risk_score", "risk_decision", "fraud_pattern", "is_fraud", "fraud_label"}
        self.assertFalse(prohibited.intersection(main.FEATURE_NAMES))

    def test_no_previous_transaction_means_no_user_history(self):
        past = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        current = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        history = [{
            "amount": 100,
            "beneficiary_id": "existing-beneficiary",
            "sender_device_id": "device-1",
            "location_name": "Haldia",
            "transaction_time": past,
        }]
        profile = {
            "average_amount": 100,
            "known_devices": ["device-1"],
            "familiar_locations": ["haldia"],
            "common_hours": [12],
        }
        features = main.build_current_ml_features(
            user_id="demo", amount=200, transaction_time=current,
            category="P2P", device="device-1", location_name="Haldia",
            latitude=None, longitude=None, beneficiary_id="new-beneficiary",
            history=history, profile=profile,
        )
        self.assertEqual(features["unknown_beneficiary"], 1)
        self.assertEqual(features["no_previous_transaction"], 0)
        self.assertEqual(features["amount_deviation_pct"], 100)

    def test_model_loading_checks_feature_schema(self):
        with patch("joblib.load", return_value={"feature_names": ["wrong"]}):
            with self.assertRaisesRegex(ValueError, "feature schema"):
                main.load_trained_model("untrusted-model-is-never-opened-in-test.joblib")

    def test_new_notifications_do_not_mutate_synthetic_source(self):
        collection = MagicMock()
        with patch.object(main, "get_collection", return_value=collection) as get_collection:
            main.create_notification("demo", "tx-1", "Test", "Demo", "LOW")
        get_collection.assert_called_once_with("notifications")
        collection.insert_one.assert_called_once()

    def test_anomaly_score_is_not_fraud_probability(self):
        with patch.object(main, "ML_CLASSIFIER", None):
            self.assertIsNone(main.predict_ml_risk({}))


if __name__ == "__main__":
    unittest.main()
