# SecureFlow-AI · Hackathon demo runbook

**Team QuantumMinds | Interactive synthetic-payment sandbox**

SecureFlow-AI is a working prototype for investigating how context-aware payment risk could be assessed **before an authorized payment**. No money moves. The profile data is synthetic. A rule engine runs even when an optional supervised ML model is not loaded.

## Pre-flight (10 minutes)

1. Start the local FastAPI backend (`backend/README` settings are in the root [README](README.md)). Confirm `http://127.0.0.1:8000/health` returns `api: OK` and `mongodb: OK`.
2. Run `npm ci` and `npm run dev` in `frontend/`; open the Vite URL (usually `http://localhost:5173`).
3. Confirm the blue **Interactive Sandbox** strip reads **Engine Connected**; note whether the optional ML classifier is actually loaded. If not, say “rules-only mode.”
4. Select a synthetic user who has a meaningful transaction history. The three demo presets use that user's known device, familiar location and average amount when present.
5. Do a rehearsal with each preset. **Never promise a preset will produce a particular decision**: risk scores are calculated from historical records and entered context.

## 90-second presentation

| Timing | What to show | What to say |
|---|---|---|
| 0–15 sec | Dashboard and sandbox banner | “We assess simulated payment attempts against a user's historical context. This is not connected to real UPI payments.” |
| 15–35 sec | Everyday payment → Analyze | “Nine rule inspectors each contribute bounded points. We can explain the score and inspect the signals beneath each factor.” |
| 35–55 sec | New device → Analyze | “A different context can change risk even when the amount stays similar; missing signals are marked unavailable rather than invented.” |
| 55–75 sec | High-value anomaly → Analyze | “An unusual amount, unrecognized device and new recipient are evaluated as a combination; our policy can allow, require verification, or block.” |
| 75–90 sec | Risk explanation, recent activity, health | “Every attempt is auditable. Only approved/monitored events help shape future normal behavior. ML and database readiness are visible.” |

If the engine requests verification, explain that the question is selected by the server and answer evaluation is a **prototype challenge**, not identity proof. If an event is blocked, show the incident and draft report records through the API or SOC routes as available. Do not claim any authority received a report.

## Judge questions (concise answers)

**Does the model actually work?** Nine rule-based factors are implemented and tested. Optional supervised ML can be loaded from a **locally trained** joblib artifact. The UI explicitly indicates when there is no classifier; it then shows `N/A — rules only` rather than a fake probability.

**How do you avoid fake “AI accuracy”?** We train only on explicit `is_fraud` labels, not on previous rule decisions or risk scores. The provided CSV's original split overlaps nearly all test users with training; our updated default holds out entire user IDs. We report precision, recall, F1, false-positive rate, PR-AUC and test metrics on synthetic data, with explicit overlap auditing. Real-world quality and probability calibration are not established.

**What were the disjoint test results?** With a reproducible 40-tree demonstration run: 89.66% precision, 83.87% recall and 86.67% F1 on 148,750 synthetic test transactions, including 310 fraud labels from 1,512 users never seen in training. These remain generated-data results only.

**Could users trick the recipient score?** Supplying a fake `beneficiary_id` no longer makes a recipient trusted. The backend resolves known beneficiaries against server-held records and transaction history.

**How does history evolve?** The synthetic source data is untouched. New simulated events are stored separately. Approved/monitored ones can inform the future baseline; pending, blocked or rejected attempts cannot normalize fraudulent behavior.

**Is it ready for public launch?** No. End-user auth, role-based SOC authorization, consent/data retention policies, robust verification, live PSP integrations, rate-limiting, and external security testing are required. The backend blocks non-health endpoints outside development until real authentication is implemented.

## Validation commands

```sh
cd backend
python -m unittest discover -s tests -v
cd ../frontend
npm ci
npm run lint
npm test
npm run build
```

GitHub Actions runs these checks automatically on the draft pull request. Merge only after team review and a full local demo with your own Atlas connection.
