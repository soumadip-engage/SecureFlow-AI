# Safe SQLite → MongoDB development importer

The read-only audit found `SecureFlowAI_DevTest` has **empty synthetic collections**. This importer seeds them with the 6,000-transaction SQLite demo export so that the existing FastAPI `synthetic_users` and `synthetic_transactions` readers can work.

**Important safety properties**

- Targets **only** database `SecureFlowAI_DevTest` and refuses another `MONGODB_DB`
- **Dry-run by default**: opens SQLite in read-only mode, validates source table IDs and duplicate keys, never connects to MongoDB
- Requires both `--apply` and `--confirm-db SecureFlowAI_DevTest` for any write
- Upserts only *missing* documents using `$setOnInsert`, keyed by the SQLite identifier; does not replace an existing document
- Creates unique indexes for those keys in the **development synthetic collections**; no collection drops/deletes/clears
- Keeps source historical tables in `synthetic_*` collections; leaves operational `transactions`, `incidents`, `notifications` alone
- Never prints stored names, email, phone numbers, UPI IDs, device fingerprints, source values, or the MongoDB URI

## Commands (PowerShell, from `backend`)

Dry run — do this first:

```powershell
.\.venv\Scripts\python.exe tools/import_sqlite_demo.py --source "C:\Users\souma\OneDrive\Desktop\SIH ALL\SIH - SecureFlow-AI\Database of 6000 trans.db"
```

Only after reviewing output, apply to the separate development database:

```powershell
.\.venv\Scripts\python.exe tools/import_sqlite_demo.py --source "C:\Users\souma\OneDrive\Desktop\SIH ALL\SIH - SecureFlow-AI\Database of 6000 trans.db" --apply --confirm-db SecureFlowAI_DevTest
```

Verify after import:

```powershell
.\.venv\Scripts\python.exe tools/audit_mongodb.py
```

Re-running `--apply` with the same source identifiers should insert **zero** new records. If an import fails halfway, do not reset the database; review the failure and use the idempotent command again once the cause is resolved.

## Data and ML boundaries

- These are **synthetic historic user profiles**, not authenticated users. Keep them in a development-only Atlas database.
- This SQLite dataset has no independent `is_fraud` truth. Do **not** use its ALLOW/BLOCK decisions as classifier targets.
- Train from the separately labeled synthetic 1-million-row CSV and load a trusted local artifact with `ML_MODEL_PATH`.
- Do not use the synthetic contact fields for real OTP delivery, alerts, or police reports.
- The mapping copies SQLite column names as-is; cross-collection relationships remain through numeric/text IDs. The backend may still require a compatibility adjustment for fields using alternative names.

The importer is intended for **your development-only test environment**. Never run it against a real-user database.
