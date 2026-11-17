# Source database review — 6,000-transaction synthetic SQLite export

This note records a **read-only structural inspection** of the uploaded file `Database of 6000 trans(1).db` (not a live Atlas inspection). The file was opened in SQLite read-only mode. **No personal/contact values were exported.**

## Tables and counts

| Table | Rows |
| --- | ---: |
| users | 6 |
| devices | 18 |
| locations | 42 |
| beneficiaries | 18 |
| beneficiary_devices | 18 |
| transactions | 6,000 |
| risk_scores | 6,000 |
| dynamic_questions | 10 |
| verification_events | 600 |
| incidents | 92 |
| notifications | 92 |
| user_beneficiary_relationships | 60 |
| transaction_location_history | 6,000 |
| transaction_network_context | 6,000 |
| device_context_history | 6,000 |
| dataset_metadata | 14 |

Every transaction has a joined row in `risk_scores` by `transaction_id` (6,000 matches). The risk-score range is 0–100.

**Observed historical payment statuses:** ALLOWED 5,662; VERIFIED 246; HELD 68; BLOCKED 24.

**Observed risk decisions in risk_scores:** ALLOW 5,394; MONITOR 268; VERIFY 246; HOLD 68; BLOCK 24.

These are **synthetically recorded decisions**, not evidence of actual bank transfers or fraud outcomes.

## What can be reused

- Demo profile baseline: `users`, `devices`, `locations`, `user_beneficiary_relationships`
- Transaction context: `transactions`, `transaction_network_context`, `device_context_history`, `transaction_location_history`
- Explainable-risk demonstration: `risk_scores` and `dynamic_questions`
- Incident / simulated notification history: `incidents`, `notifications`

The `users` rows contain example email, phone and emergency-contact *fields*; those are **not** evidence that OTP verification, face matching, backup-contact consent, or alert delivery is implemented. Existing `notifications.delivery_status` entries say `SIMULATED_DELIVERED`.

The `risk_scores` table also has an `identity_risk` field. This should not silently change the team's agreed **nine-factor / 100-point** scoring model; map or discard this historical extra field explicitly and test score compatibility.

## Critical ML training finding

Neither `transactions` nor `risk_scores` has an independent `is_fraud` / `fraud_label` field. **Do not train supervised fraud classification on ALLOW, VERIFY, HOLD or BLOCK as labels.** They are the *outcomes* of a policy and would make the classifier imitate that policy.

Use the separately provided synthetic **1-million-row labeled CSV** for controlled supervised ML training. The existing offline trainer offers a user-disjoint holdout. Evaluate data leakage and calibration before interpreting model probability as actionable risk. The 6,000-row database is useful for replay, feature compatibility testing and seeded user histories, not independent fraud ground truth.

## Atlas compatibility still unknown

The inspected file is SQLite. We have **not** established that the contents, field spellings or values have been imported into `SecureFlowAI_DevTest` MongoDB Atlas. Run the safe audit from [DATABASE_AUDIT.md](DATABASE_AUDIT.md) locally and confirm actual collection presence before building a migration or onboarding against assumed MongoDB documents.

## Implementation priorities

1. User authentication must live in its own accounts collection; never treat `synthetic_users` as authenticated identities.
2. Server-approved simulated payments must have authoritative lifecycle states (submitted, held, allowed, blocked) with idempotency and audit tests.
3. ML inference should be explicitly loaded from a locally trained, trusted, versioned artifact; fail safely to rules-only with truthful UI status.
4. Contact OTP, passkeys, consented backup notifications and reporting require genuine provider/permission integration, not boolean fields controlled by a browser.
