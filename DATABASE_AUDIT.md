# Database audit — SecureFlow-AI development

The app currently reads historical profiles from `synthetic_users` and past payments from `synthetic_transactions`. It writes fresh sandbox outcomes to separate operational collections. A new user-registration schema should **not** be invented based on these collection names alone.

## First run: confirm what actually exists

With the backend's existing Python 3.13 environment and `backend/.env` (pointing to a separate *development* database), from `backend/` run:

```powershell
.\.venv\Scripts\python.exe tools/audit_mongodb.py
```

This command only executes MongoDB `ping`, `list_collection_names`, `estimated_document_count`, and bounded `find` calls. It does not change records or create indexes.

**Privacy:** It reports collection names, approximate counts and presence of a small set of expected field names. It does **not** print emails, phone numbers, UPI IDs, IP addresses, transaction amounts, stored documents, or credentials. Even so, review the output before sharing it externally.

If the audit reports an empty `SecureFlowAI_DevTest`, this is expected: connecting Atlas does not automatically copy the team's 6,000-transaction historical dataset. Do not point scripts at a shared production database and do not reset your main database.

## What we check before migrations

1. Whether there are actual synthetic users and transactions in the selected development database
2. Whether transactions contain explicit `is_fraud` / `fraud_label` supervision, rather than only rule-engine decisions
3. Whether operational collections overlap or conflict with historical data
4. Whether the training CSV and database feature schemas can be mapped without guessing
5. Whether a dedicated user-account store is needed (it is: the existing `synthetic_users` are demo personas, not authenticated accounts)

## Development milestones (honest boundaries)

- [x] Verify Atlas connectivity and add a privacy-safe collection audit
- [ ] Inspect audit results and map source records to model features
- [ ] Train and load trusted local supervised model; verify `/health` returns `ml: READY`
- [ ] Introduce registration/login with password hashing, short-lived server-side sessions and per-user access checks
- [ ] Integrate real email/phone OTP delivery **only after** configuring a trusted provider and abuse protections
- [ ] Add WebAuthn/passkey authentication; never store raw fingerprint templates
- [ ] Enforce simulated-payment allow/hold/block and test all state transitions
- [ ] Add verified, consented emergency-contact alerts and incident review workflow
- [ ] Integrate authorized external payment and police/reporting providers only with appropriate agreements

Don't represent unconfigured OTP, biometric or external reporting providers as operational in the UI.
