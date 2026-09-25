# SIH146 — AI Powered Monitoring and Analysis of Bitcoin Transactions

## Problem
Continuously monitoring cryptocurrency transactions and manually spotting suspicious patterns is
slow and error-prone. Bitcoin data is highly interconnected, and suspicious activity may not be
visible in a single transaction: it can require examining multiple transactions, wallets, network
information and timestamps together (unusual behaviour, fund movement across wallets, links between
otherwise unrelated wallets).

## Solution
A local, offline-first prototype of an AI/ML-powered Bitcoin transaction monitoring and forensic
investigation platform. It analyses **synthetic** transaction and network metadata, identifies
anomalous patterns (Isolation Forest plus forensic behavioural rules), ranks investigative leads,
groups related wallets, and presents explainable findings to an investigator, who alone decides what
becomes a case. The system does **not** claim that an anomaly proves criminal or fraudulent
activity; it flags unusual behaviour for investigator review.

Read this first:
- **All data shown is synthetic.** IP addresses, devices and sessions are synthetic demo observations
  (documentation IP ranges only), not derived from the blockchain. Bitcoin transactions contain no IP,
  device or session information.
- **A flagged wallet or a lead is not a case.** Cases exist only because an investigator created one.
- **"Prototype fusion weighting — not statistically validated."** The combined result (40% forensic
  rule share + 60% ML score) and the priority bands (top 5% High, next 15% Medium, rest Low) are prototype
  settings, not validated measures.
- A real Bitcoin source exists as an **optional, off-by-default adapter** (see below). Nothing in the
  prototype needs the internet.

## Current implementation status
| Component | Status |
|---|---|
| Synthetic dataset generator + CSV (10,000 rows, 5,000 transfers, 410 wallets, Oct 2025 – Sep 2026) | Tested implementation |
| Feature engineering, Isolation Forest, forensic rules, result fusion (`ml/`, CLI + CSV outputs) | Tested implementation (unchanged) |
| Backend API (FastAPI) + SQLite database, 50 endpoints | Tested implementation |
| Ingestion: synthetic CSV, synthetic stream, inbox folder, API | Tested implementation |
| Continuous monitoring with automatic (micro-batch) analysis | Tested implementation |
| Synthetic network metadata (IP / device / session observations) | Tested implementation |
| Investigative leads and priority ranking | Tested implementation (prototype bands) |
| Related-entity clustering (shared network observation; Louvain transaction communities) | Tested implementation |
| Relationship graph API and UI (React Flow) | Tested implementation |
| Unified search (wallets, transactions, clusters, IPs, devices, sessions, cases) | Tested implementation |
| Case management with saved evidence, notes and history | Tested implementation |
| Investigator UI (Dashboard, Anomalies + leads, Wallet, Cases, Transactions / Network + clusters, Settings) | Tested implementation |
| Local / offline operation, with CSV fallback when the backend is not running | Tested implementation |
| Real Bitcoin source (Esplora-compatible, read-only) | Fully implemented as an optional adapter; **off by default**; verified live on one block; not shown in the UI |
| Authentication, multi-user, deployment hardening | Not implemented (single-user local prototype) |
| Cytoscape.js graph | Not used: the graph is built with React Flow |

## Quick start (Windows / PowerShell)
Requirements: Python 3.14 (a `.venv` is expected in the project root) and Node.js with npm.

```powershell
python -m venv .venv                               # only if .venv does not exist
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
cd frontend; npm install; cd ..
```

Terminal 1 — the backend (http://127.0.0.1:8000, interactive API docs at `/docs`):
```powershell
.\.venv\Scripts\python.exe -m backend
```
Terminal 2 — the UI (http://localhost:5173; `--host` also serves it to a phone on the same network):
```powershell
cd frontend
npm run dev
```
The first time, the app shows an "Offline mode" banner with **Load synthetic demo data**: it imports the
synthetic transactions and network observations and runs the analysis (about 30 seconds). Everything is
stored in `data/sih146.db` (git-ignored). If the backend is not running, the UI still opens and shows the
bundled pipeline CSVs read-only (no cases, clusters or monitoring).

Command line equivalents:
```powershell
.\.venv\Scripts\python.exe -m backend.cli init-db
.\.venv\Scripts\python.exe -m backend.cli import-csv [--replace]     # synthetic transactions (safe to repeat)
.\.venv\Scripts\python.exe -m backend.cli import-network             # synthetic IP/device/session observations + clusters
.\.venv\Scripts\python.exe -m backend.cli analyze                    # run the analysis now
.\.venv\Scripts\python.exe -m backend.cli stats
```

## Demo walkthrough (about 5 minutes)
1. Start both servers; load the demo data if asked. **Dashboard:** transactions, wallets, anomalies, leads,
   active cases (0), clusters, and the monitoring strip ("Synthetic dataset — no real blockchain data is being monitored").
2. **Anomalies:** the lead queue (42) ranked by the prototype combined result. Expand the top lead to see why it
   was flagged (Isolation Forest score, the triggered rules and their values against the population 90th percentile).
3. Open the wallet: evidence, behavioural profile, counterparty graph, **Related entities** (clusters, synthetic
   devices/IPs, optional infrastructure graph).
4. **Create case** from the lead (optionally attaching a cluster). The evidence is saved with the case.
5. **Cases:** add a note, mark the evidence reviewed, move it to *Under investigation*; check the history.
6. **Transactions / Network → Clusters:** open a cluster and a shared synthetic device.
7. Sidebar **search:** try `wallet_350`, `syn-004997`, `203.0.113.48`, `dev-0318`, `CASE-0001`.
8. **Settings:** switch on the synthetic stream, watch the Dashboard's last transaction / last analysis move and
   new leads appear; the saved evidence in your case does not change.
9. Stop and restart the backend: the case, notes, history and settings are still there.

An automated version of this path runs against a throw-away database and prints every check:
```powershell
.\.venv\Scripts\python.exe scripts\e2e_check.py
```

## Project structure
```
.
├── .venv/                       # virtual environment (git-ignored)
├── dataset/synthetic_bitcoin_transactions.csv   # generated raw data (10,000 rows, 7 columns)
├── data/
│   ├── wallet_behavior_features.csv   anomaly_results.csv   forensic_results.csv   fusion_results.csv
│   ├── synthetic_network_observations.csv       # SYNTHETIC IP/device/session observations (6,489 rows)
│   └── sih146.db                                # local database (created on first run, git-ignored)
├── ml/                          # the original pipeline (unchanged): features, Isolation Forest, rules, fusion
├── generate_dataset.py          # generates and validates the raw dataset
├── backend/                     # FastAPI + SQLite platform (see architecture.md)
│   ├── main.py  config.py  database.py  models.py  schemas.py  cli.py
│   ├── ingestion/               # TransactionSource implementations (synthetic CSV, stream, inbox, real Bitcoin)
│   ├── analysis/                # bridge to ml/, fusion/priority, clustering
│   ├── services/                # ingest, monitor, leads, graph, cases, search, real source, settings
│   ├── routers/                 # the API endpoints
│   └── tests/                   # 331 backend tests
├── frontend/                    # Vite + React + TypeScript investigator UI
├── scripts/e2e_check.py         # end-to-end check / demo
├── requirements.txt  requirements-dev.txt  pytest.ini  .env.example
├── README.md   architecture.md   session.md
```

## The original ML pipeline (command line, unchanged)
The platform calls these same functions; results for all 410 wallets were verified identical to the CSVs
below (features, scores, predictions, rules, combined result). Run from the project root:

**1. Dataset generator** — regenerates `dataset/synthetic_bitcoin_transactions.csv` (fixed seed 42) and prints a validation summary.
```powershell
.\.venv\Scripts\python.exe generate_dataset.py
```
**2. Feature engineering** (raw CSV → `data/wallet_behavior_features.csv`)
```powershell
.\.venv\Scripts\python.exe ml\feature_engineering.py
```
**3. Anomaly detection** (features → `data/anomaly_results.csv`): `wallet_address`, `anomaly_score`
(higher = more unusual), `anomaly_prediction` (`Anomalous` / `Normal`).
```powershell
.\.venv\Scripts\python.exe ml\anomaly_detection.py
```
**4. Forensic behavioural rules** (features + anomaly results → `data/forensic_results.csv`): `rule_count`,
`evidence_level` (how many indicators triggered; not a risk score), `triggered_rules`, `explanation`.
Triggered rules are behavioural indicators of unusual behaviour, not proof of criminal activity.
```powershell
.\.venv\Scripts\python.exe ml\forensic_rules.py
```
**5. Result fusion** (forensic + ML → `data/fusion_results.csv`): a weighted combination (prototype weights, not validated).
```powershell
.\.venv\Scripts\python.exe ml\result_fusion.py
```
Raw dataset time window: 2025-10-01 to 2026-09-24 (about one year, roughly balanced across months; synthetic,
not real blockchain data). Raw dataset columns (fixed): `timestamp`, `wallet_address`, `amount_btc`,
`direction`, `input_count`, `output_count`, `counterparty_wallet`.

## Backend
`python -m backend` serves the API on `127.0.0.1:8000` (local only). Main areas (full list at `/docs`):
| Area | Endpoints |
|---|---|
| Overview, health | `/api/health`, `/api/overview` |
| Transactions, wallets | `/api/transactions` (list, get, create, batch), `/api/wallets`, `/api/wallets/{a}/analysis`, `/api/wallets/{a}/review` |
| Import | `/api/import/synthetic-csv`, `/api/import/synthetic-network` |
| Analysis, leads | `/api/analysis/run|runs|latest|wallets`, `/api/leads`, `/api/leads/{wallet}` |
| Monitoring | `/api/monitor/status|start|stop|tick|config|stream/scenario` |
| Network (synthetic), clusters, graph | `/api/network/observations`, `/api/network/entities/{type}/{id}`, `/api/clusters`, `/api/graph` |
| Cases, search | `/api/cases` (+ items, notes, reviews, history, transactions, graph), `/api/search` |
| Settings, sources | `/api/settings`, `/api/sources`, `/api/sources/real-bitcoin`, `/api/sources/real-bitcoin/fetch` |

Errors always have the same shape: `{"error": {"code", "message", "details"?}}`.

**Monitoring.** A background thread checks every few seconds (default 5) for new input: files dropped into
`data/stream_inbox/`, the optional synthetic stream, and transactions posted to the API. When new transactions
have arrived it re-runs the whole pipeline (features → Isolation Forest → rules → fusion → leads → clusters).
This is *micro-batch* analysis, not per-transaction: the model and the percentile rules are relative to the
whole population and a run takes several seconds. The source is always labelled synthetic.

**Leads.** A wallet is an investigative lead if the Isolation Forest flags it or it falls in the High priority
band (prototype bands, not validated). Wallets with fewer than two transactions cannot be scored (their time
features do not exist) and are reported as unscored, never filled in; at least 20 scoreable wallets are required.

**Configuration.** Copy `.env.example` to `.env` (git-ignored) to change anything; every value is optional.
Never commit secrets. Monitor on/off, interval, automatic analysis and the synthetic stream are also
changeable in the UI (Settings) and are saved in the database.

## Frontend
Vite + React + TypeScript, Recharts, React Flow. Same five sidebar items: Dashboard, Anomalies, Cases,
Transactions / Network, Settings, in light and dark themes. Leads live inside Anomalies; clusters and synthetic
network entities live inside Transactions / Network (a "Clusters" tab) and on wallet pages; search is in the sidebar.
```powershell
cd frontend
npm run dev        # http://localhost:5173 (proxies /api to the backend on port 8000)
npm run build      # type-check + production build
```
Exports (CSV/JSON) are generated locally in the browser. Cases are stored in the backend database, not the
browser (cases saved in the browser by early prototype versions were test data and are not shown or migrated).

## Optional real Bitcoin source
Off by default; the platform never needs it. It reads recent confirmed blocks from an Esplora-compatible public
API (default `https://blockstream.info/api`, read-only requests) and stores them with `source = real_bitcoin`,
separate from the synthetic data. To try it, set `SIH146_REAL_BITCOIN_ENABLED=true` in your local `.env`
(see `.env.example`; an optional API key also goes only there), restart, then:
```powershell
.\.venv\Scripts\python.exe -m backend.cli fetch-real --blocks 1 --max-tx-per-block 150
.\.venv\Scripts\python.exe -m backend.cli analyze --source real_bitcoin
```
What to know: a Bitcoin transaction has many inputs and outputs, so each is simplified to sender → receiver
(largest input address to each non-change output address; not entity attribution). Real wallets get no IP,
device or session data. 14 of the 18 model features can be computed for any real wallet; four time-gap
features need two or more transactions, and wallets without them are left unscored. Only the first N
transactions of each block are read (a bounded slice, not a random sample), and every transaction in a block
shares its timestamp, so read several blocks for meaningful timing. The UI shows synthetic data only.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest        # 342 tests (331 backend + 11 pipeline); ~10 minutes
.\.venv\Scripts\python.exe scripts\e2e_check.py
cd frontend; npm run build
```
No test needs the internet. If a run looks stuck on a laptop, check that the machine did not go to sleep.

## Known limitations
- Synthetic data only in every screen; the anomaly rate reflects the model's 10% contamination setting, not a
  measured prevalence.
- Analysis is micro-batch (about 10 seconds per run), not per transaction.
- Single user, no authentication; the API listens on localhost only.
- The layout targets desktop and tablet widths; narrow phone widths overflow horizontally.
- Clusters and shared synthetic devices show connected activity or shared synthetic infrastructure, never common ownership.
- Real-data features come from a small slice of the chain and are for demonstrating the adapter, not for conclusions.

## More information
- Technical architecture, data model, ML pipeline, API and design decisions: [architecture.md](architecture.md)
- Development history and checkpoint log: [session.md](session.md)
