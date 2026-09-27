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
| Backend API (FastAPI) + SQLite database, 61 endpoints, 30 tables | Tested implementation |
| Ingestion: synthetic CSV, synthetic stream, inbox folder, API | Tested implementation |
| Rich address-level model, offline GeoIP, CSV/JSON/JSONL/XML import (Phase 1, synthetic; not used by the analysis) | Tested implementation |
| Common-input-ownership address entities + network correlation (Phase 2, synthetic; separate from the wallet-level pipeline; UI: Network "Entities" tab) | Tested implementation |
| Peeling-chain / CoinJoin-like detection + on-demand risk propagation (Phase 3, synthetic; UI: badges, related-blocks, Anomalies filter, Wallet Detail panel) | Tested implementation |
| Explainable confidence score, typology tags, GeoIP geo aggregation (Phase 4, synthetic; UI: Dashboard, Anomalies, Wallet Detail) | Tested implementation |
| Continuous monitoring with automatic (micro-batch) analysis | Tested implementation |
| Synthetic network metadata (IP / device / session observations) | Tested implementation |
| Investigative leads and priority ranking | Tested implementation (prototype bands) |
| Related-entity clustering (shared network observation; Louvain transaction communities) | Tested implementation |
| Relationship graph API and UI (React Flow) | Tested implementation |
| Unified search (wallets, transactions, clusters, IPs, devices, sessions, cases) | Tested implementation |
| Case management with saved evidence, notes and history | Tested implementation |
| Investigator UI (Dashboard, Anomalies + leads, Wallet, Cases, Transactions / Network + clusters + entities, Settings) | Tested implementation |
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

## Quick start (Linux / macOS)
Requirements: Python 3.14 (a `.venv` is expected in the project root) and Node.js with npm. Every command below is
the exact same underlying command as the Windows section above; only the venv path convention differs
(`.venv/bin/python` instead of `.venv\Scripts\python.exe`). Nothing in the codebase is Windows-only — this project
runs unchanged on Linux and macOS.

Four scripts wrap the commands below for convenience (`scripts/setup.sh`, `scripts/run_backend.sh`,
`scripts/run_frontend.sh`, `scripts/run_tests.sh`); run with `bash scripts/<name>.sh` or, after
`chmod +x scripts/*.sh`, with `./scripts/<name>.sh`.

```bash
python3 -m venv .venv                               # only if .venv does not exist
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
cd frontend && npm install && cd ..
```
(equivalent: `bash scripts/setup.sh`)

Terminal 1 — the backend (http://127.0.0.1:8000, interactive API docs at `/docs`):
```bash
./.venv/bin/python -m backend
```
(equivalent: `bash scripts/run_backend.sh`)

Terminal 2 — the UI (http://localhost:5173; add `-- --host` to `npm run dev` to also serve it to a phone on the same network):
```bash
cd frontend
npm run dev
```
(equivalent: `bash scripts/run_frontend.sh`)

The first time, the app shows an "Offline mode" banner with **Load synthetic demo data**: it imports the
synthetic transactions and network observations and runs the analysis (about 30 seconds). Everything is
stored in `data/sih146.db` (git-ignored). If the backend is not running, the UI still opens and shows the
bundled pipeline CSVs read-only (no cases, clusters or monitoring).

Command line equivalents:
```bash
./.venv/bin/python -m backend.cli init-db
./.venv/bin/python -m backend.cli import-csv [--replace]     # synthetic transactions (safe to repeat)
./.venv/bin/python -m backend.cli import-network             # synthetic IP/device/session observations + clusters
./.venv/bin/python -m backend.cli analyze                    # run the analysis now
./.venv/bin/python -m backend.cli stats
```
Every other command in this README (`ml/*.py`, `generate_dataset.py`, `generate_rich_dataset.py`, the Phase 2-4
`backend.cli` subcommands, `pytest`, `scripts/e2e_check.py`) follows the same translation: replace
`.\.venv\Scripts\python.exe` with `./.venv/bin/python` and backslash path separators (`ml\feature_engineering.py`,
`dataset\rich\...`) with forward slashes (`ml/feature_engineering.py`, `dataset/rich/...`).

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
Linux / macOS: `./.venv/bin/python scripts/e2e_check.py` (or `bash scripts/run_tests.sh`, which also runs the full test suite first).

## Project structure
```
.
├── .venv/                       # virtual environment (git-ignored)
├── dataset/synthetic_bitcoin_transactions.csv   # generated raw data (10,000 rows, 7 columns)
├── dataset/rich/                # SYNTHETIC address-level dataset (Phase 1): jsonl, csv, samples in 4 formats, ground truth (validation only)
├── data/
│   ├── wallet_behavior_features.csv   anomaly_results.csv   forensic_results.csv   fusion_results.csv
│   ├── synthetic_network_observations.csv       # SYNTHETIC IP/device/session observations (6,489 rows)
│   ├── geoip/                                   # DB-IP Lite country + ASN databases (CC BY 4.0), used offline
│   └── sih146.db                                # local database (created on first run, git-ignored)
├── ml/                          # the original pipeline (unchanged): features, Isolation Forest, rules, fusion
├── generate_dataset.py          # generates and validates the raw dataset
├── generate_rich_dataset.py     # generates dataset/rich/ (seed 149, deterministic)
├── backend/                     # FastAPI + SQLite platform (see architecture.md)
│   ├── main.py  config.py  database.py  models.py  schemas.py  cli.py
│   ├── ingestion/               # TransactionSource implementations (synthetic CSV, stream, inbox, real Bitcoin)
│   ├── analysis/                # bridge to ml/, fusion/priority, clustering
│   ├── services/                # ingest, monitor, leads, graph, cases, search, real source, settings
│   ├── routers/                 # the API endpoints
│   └── tests/                   # 480 backend tests
├── frontend/                    # Vite + React + TypeScript investigator UI
├── scripts/e2e_check.py         # end-to-end check / demo
├── scripts/*.sh                 # Linux / macOS setup + run + test scripts (Windows: use the .venv\Scripts\... commands directly)
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
| Rich transactions (Phase 1) | `/api/import/rich`, `/api/transactions/{id}/details`, `/api/geoip/status` |
| Address entities + correlation (Phase 2, synthetic; UI: Network "Entities" tab) | `/api/entities`, `/api/entities/{id}`, `/api/entities/run`, `/api/entity-graph` |
| Pattern detectors (Phase 3, synthetic; UI: Related entities, Anomalies, transaction badge, Wallet Detail) | `/api/peeling-chains`, `/api/peeling-chains/{id}`, `/api/coinjoin-candidates`, `/api/risk/propagate` |
| Wallet insights: confidence score, typology, geo (Phase 4, synthetic; UI: Dashboard, Anomalies, Wallet Detail) | `/api/confidence-scores`, `/api/wallets/{id}/confidence`, `/api/wallets/{id}/typology`, `/api/wallets/{id}/geo`, `/api/geo/summary` |

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
network entities live inside Transactions / Network (a "Clusters" tab) and on wallet pages; address entities from
the common-input-ownership heuristic live in a 4th "Entities" tab on the same page; search is in the sidebar.
Peeling chains, CoinJoin-like candidates and risk propagation (Phase 3) surface as: a "possible CoinJoin" badge on
transaction rows, a peeling-chain / address-entity related-block on wallet pages, a "Part of a peeling chain"
filter on Anomalies, and an on-demand risk-propagation panel on Wallet Detail. Cases can now hold an address entity
as an item, alongside leads, wallets, transactions and clusters.
```powershell
cd frontend
npm run dev        # http://localhost:5173 (proxies /api to the backend on port 8000)
npm run build      # type-check + production build
```
Exports (CSV/JSON) are generated locally in the browser. Cases are stored in the backend database, not the
browser (cases saved in the browser by early prototype versions were test data and are not shown or migrated).

## Rich transactions, GeoIP and multi-format import (Phase 1)
A second, **synthetic** address-level view of every transaction: txid, fee, script type, input and output addresses with amounts, and a synthetic network
flow (client IP, node IP, ports, country, ASN). It is stored in four extra tables beside the existing model and is **not used by the analysis**: the results
(410 wallets, 41 flagged, 42 leads, 52 clusters) are unchanged. IP addresses are randomly assigned synthetic values sampled from public ranges; they are not
observed traffic and no real person or network did anything. Details and the field list: [dataset/rich/README.md](dataset/rich/README.md).
```powershell
.\.venv\Scripts\python.exe generate_rich_dataset.py             # regenerate dataset/rich/ (deterministic, seed 149; 1-2 minutes; add --verify to prove it)
.\.venv\Scripts\python.exe -m backend.cli import-rich dataset\rich\synthetic_rich_transactions.jsonl
.\.venv\Scripts\python.exe -m backend.cli import-rich dataset\rich\sample.xml --format xml
```
Formats: **CSV** (list fields as JSON arrays in the cells), **JSON** (array or `{"transactions": [...]}`), **JSONL** and **XML** (DTDs and entities are rejected).
API: `POST /api/import/rich?format=csv|json|jsonl|xml` (raw body, max 30 MB), `GET /api/transactions/{id}/details`, `GET /api/geoip/status`. Import attaches the
detail to the existing transaction (nothing is duplicated), is idempotent, and reports rejected records one by one.

**GeoIP credit: IP geolocation by DB-IP.com** (https://db-ip.com), DB-IP Lite country and ASN databases, licensed CC BY 4.0 (`data/geoip/`, see `data/geoip/ATTRIBUTION.txt`).
The files are used offline; if they are missing the app still works and lookups return empty values.

## Address entities and network correlation (Phase 2)
Built entirely on the Phase 1 rich data (never on `sender_wallet`/`receiver_wallet`/`amount_btc` or the ground-truth file). Two
things, computed as a separate step, not by the monitor or the wallet-level analysis run:
- **Common-input-ownership entities**: addresses spent together as inputs of one transaction are grouped as one likely-same-
  controller entity (`CIO-<lowest address>`). A heuristic -- likely common control, never proof; broken by CoinJoin-like
  transactions (excluding those is future work).
- **Network correlation**: entity-to-IP links and entity-to-entity links via a shared synthetic IP, plus findings such as an IP
  used by several entities, an entity seen from several countries/ASNs, or an unusual destination port. Evidence, never a verdict.

Stored in 5 new additive tables (`address_entities`, `address_entity_members`, `entity_ip_links`, `entity_links`,
`correlation_findings`). UI added in Phase 3 Part B: a 4th "Entities" tab on Transactions / Network, and an
"Address entity" related-block on wallet pages (`GET /api/entities` gained a read-only `wallet` filter and a
`linked_wallets` field for this -- purely presentational, never fed back into the entity-building union-find).
```powershell
.\.venv\Scripts\python.exe -m backend.cli build-entities
```
or `POST /api/entities/run`. Read with `GET /api/entities`, `GET /api/entities/{id}`, `GET /api/entity-graph` (a separate graph
from the wallet-level `/api/graph`). Offline validation against the known ground truth (purity/completeness, **not** read by the
backend): `.\.venv\Scripts\python.exe scripts\validate_entities.py`.

## Pattern detectors: peeling chains, CoinJoin-like transactions, risk propagation (Phase 3)
Three heuristic detectors -- investigative signals for a human to check, never proof of anything. Not hooked into the wallet-level
analysis run or the monitor.
- **Peeling chains** (`transactions` table only): a wallet forwards most of what it just received, in one transaction, to the next
  wallet, repeated for 3+ hops (a large balance walked down a chain). Stored in `peeling_chains` / `peeling_chain_hops`.
- **CoinJoin-like candidates** (rich data): several distinct input addresses spent together with several outputs of about the same
  value. Stored in `coinjoin_candidates`. Flagged transactions are then excluded from Phase 2's common-input-ownership entities
  (re-run `build-entities` after `detect-patterns` for this to take effect; if `detect-patterns` was never run, entities behave
  exactly as in Phase 2).
- **Risk propagation** (on demand, nothing stored): from one or more seed wallets (score 1.0), decayed per hop along the wallet
  transfer graph -- an investigator tool, not a validated risk score.
```powershell
.\.venv\Scripts\python.exe -m backend.cli detect-patterns
```
or `POST /api/risk/propagate` with `{"seed_wallets": ["wallet_001"]}`. Read with `GET /api/peeling-chains` (filter `wallet`; each
item's `wallets` field lists every wallet in the chain, not only its endpoints), `GET /api/peeling-chains/{chain_id}`,
`GET /api/coinjoin-candidates`. Thresholds (dominance share, minimum hops, CoinJoin input/output counts, risk decay/hops) are
prototype settings, overridable via `.env` (see `.env.example`), not statistically validated.

**UI (Phase 3 Part B):** a "possible CoinJoin" badge on transaction rows wherever `TransactionTable` is used (Network,
Case detail); a "Peeling chain membership" related-block on wallet pages, expandable to the full hop sequence; a "Part
of a peeling chain" checkbox on Anomalies; an on-demand "Risk propagation from seed wallets" panel on Wallet Detail.

## Wallet insights: confidence score, typology tags, geo aggregation (Phase 4 Part A)
Closes a gap the problem statement asks for directly: leads only carried the raw prototype `combined_score`, a blend, not a
confidence measure; GeoIP data has existed since Phase 1 with nothing surfacing it. All read-only/additive; not hooked into
the wallet-level analysis run or the monitor.
- **Confidence score**: `BASE_WEIGHT(0.55) * combined_score + ENTITY_WEIGHT(0.15 if its address entity also contains another
  flagged/lead wallet) + CORRELATION_WEIGHT(0.10 if its entity has a correlation finding) + PATTERN_WEIGHT(0.20 if it's in a
  peeling chain or a CoinJoin-like transaction)`. Weights sum to 1.0, plain module constants (same style as `fusion.py`'s, not
  config-overridable), alongside `combined_score`, never replacing it. Stored in `confidence_scores` / `confidence_signals`
  (one row per contributing signal, with its own plain-language reason -- same explainability spirit as the forensic rules).
- **Typology tags** (read-only, no new table): `Peeling chain`, `Possible CoinJoin`, `Correlated entity`, each with a reason,
  synthesized live from Phase 2/3 data.
- **Geo aggregation** (read-only, no new table): country/ASN breakdown of one wallet's traffic, and a dataset-wide summary
  across current leads, from `flow_records.geo_country/asn/asn_org` (synthetic GeoIP demo data, never observed traffic).
```powershell
.\.venv\Scripts\python.exe -m backend.cli compute-confidence
```
Run after `build-entities` and `detect-patterns`, so it can see their signals. Read with `GET /api/confidence-scores` (filter
`min_score`), `GET /api/wallets/{id}/confidence`, `GET /api/wallets/{id}/typology`, `GET /api/wallets/{id}/geo`,
`GET /api/geo/summary`.

**UI (Phase 4 Part B):** a "Confidence" column on the Dashboard priority queue and the Anomalies table; a
"Confidence" sub-section inside Wallet Detail's "Why was this wallet flagged?" panel, alongside (not replacing) the
existing ML/forensic/combined-result display; typology badges (`rule-tag` style) next to the wallet id on the
Anomalies table and Wallet Detail; a "Geographic footprint" panel on Wallet Detail; a "Top countries in leads"
panel on the Dashboard.

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
.\.venv\Scripts\python.exe -m pytest        # 525 tests (514 backend + 11 pipeline); ~15-30 minutes
.\.venv\Scripts\python.exe scripts\e2e_check.py
cd frontend; npm run build
```
Linux / macOS: `./.venv/bin/python -m pytest && ./.venv/bin/python scripts/e2e_check.py && (cd frontend && npm run build)`,
or `bash scripts/run_tests.sh` for the first two.

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
- Short technical write-up (a few pages, the approach at a glance): [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md)
- Technical architecture, data model, ML pipeline, API and design decisions: [architecture.md](architecture.md)
- Development history and checkpoint log: [session.md](session.md)
