# Development Log

Newest entries at the bottom. Add a short entry after each meaningful change; do not rewrite old ones
except to correct mistakes.

## 2026-09-21 – Synthetic raw dataset generation

1. **What changed:** Created `.venv`, `generate_dataset.py`, and `dataset/synthetic_bitcoin_transactions.csv`.
2. **Why:** First stage of the pipeline needs a reproducible raw dataset with a realistic wallet network.
3. **Implemented:** Generator with seed 42; 10,000 rows (5,000 transfers written as mirrored
   Outgoing/Incoming rows), 7 fixed columns, 410 wallets. Normal activity (communities, merchant and
   payroll hubs, slow chains) plus seven unusual patterns (rapid movement, dormant activation,
   high frequency, fan-out, fan-in, repeated relationships, unusual amounts). Built-in validation.
4. **Dependencies/files:** pandas 3.0.6 and numpy 2.5.3 installed in `.venv` only (nothing global).
5. **Validation:** Exactly 7 columns; no missing values, invalid directions, non-positive amounts or
   invalid counts; timestamps sorted; Outgoing/Incoming rows mirrored; repeated pairs and time-ordered
   3-hop paths exist. Mix by transfer: 88.3% normal, 11.7% unusual.
6. **Next step:** Feature engineering on the raw CSV (awaiting instruction).

## 2026-09-21 – Documentation and interpreter setup

1. **What changed:** Added `README.md`, `architecture.md`, `session.md`, and `.vscode/settings.json`.
2. **Why:** Keep project status, architecture and history documented; ensure VS Code uses the venv.
3. **Implemented:** Docs describing only the implemented stage (dataset generation) and marking the
   rest as planned. Workspace setting `python.defaultInterpreterPath` set to `.venv\Scripts\python.exe`.
4. **Dependencies/files:** No new dependencies. Cytoscape.js and ML libraries intentionally not installed.
5. **Validation:** `.venv` python resolves to `.venv\Scripts\python.exe` (Python 3.14.5), is a venv,
   and imports pandas 3.0.6 and numpy 2.5.3. Dataset logic untouched.
6. **Next step:** Feature engineering on the raw CSV (awaiting instruction).

## 2026-09-21 – Official PS146 context added to documentation

1. **What changed:** The official PS146 problem/solution description and planned architecture were added to the project documentation.
2. **Why:** The documentation previously had only a generic description because the official PS146 wording had not yet been provided.
3. **Implemented:** `README.md` and `architecture.md` were rewritten with the official project title, problem, solution, planned ML pipeline, feature groups, graph concept and planned dashboard structure. Implemented, Planned and Future/optional items are labelled separately. The README's PS146 TODO placeholder was removed.
4. **Dependencies/files/architecture:** No code, dependency or architecture changes. `generate_dataset.py` and the dataset were not touched. The docs state the current dataset has only the 7 raw columns and no IP/network metadata.
5. **Validation:** Documentation only; no code run. Status was checked against the implemented list (venv, pandas/numpy, dataset generator, docs).
6. **Next step:** The project remains at the synthetic raw dataset stage; feature engineering is next (awaiting instruction).

## 2026-09-21 – Feature engineering

1. **What changed:** Added `ml/feature_engineering.py`; generated `data/wallet_behavior_features.csv`. README.md and architecture.md updated to show feature engineering as implemented.
2. **Why:** Isolation Forest needs wallet-level behavioural features, not raw transactions.
3. **Implemented:** Script converts the 10,000 raw rows into 410 wallet rows with 18 features (transaction, time, flow, network groups). Raw CSV read-only and unchanged. activity_burst = max transactions in any 1-hour window; hop_distance = simplified mean undirected shortest-path hops to reachable wallets (BFS, ignores time). No new dependencies.
4. **New files:** `ml/feature_engineering.py`, `data/wallet_behavior_features.csv`. The raw CSV is still at `dataset/`, not `data/`; the script reads `data/` first and falls back to `dataset/`.
5. **Validation:** Runs without errors; built-in checks pass (all wallets present, counts add up to 10,000 rows, numeric dtypes, 0 missing values in all features).
6. **Next step:** Isolation Forest on the feature dataset (awaiting instruction).

## 2026-09-21 – Isolation Forest anomaly detection

1. **What changed:** Added `ml/anomaly_detection.py`, generated `data/anomaly_results.csv`, installed scikit-learn in `.venv`. README.md and architecture.md updated.
2. **Why:** Next pipeline stage: find wallets whose behaviour is unusual relative to the wallet population.
3. **Implemented:** Unsupervised IsolationForest (n_estimators=200, contamination=0.10, random_state=42) on the 18 numeric wallet features; wallet_address excluded, no labels used, no scaling needed. anomaly_score = -score_samples (higher = more unusual); anomaly_prediction = Anomalous/Normal from the model's own threshold. contamination=0.10 is a prototype assumption for review-queue size, not a measured fact.
4. **Files/dependencies:** New `ml/anomaly_detection.py`, `data/anomaly_results.csv`. scikit-learn 1.9.1 (plus scipy 1.18.1) installed in `.venv` only. Raw CSV and `wallet_behavior_features.csv` not modified.
5. **Validation:** 410 wallets analysed, 0 missing values in output; 41 Anomalous (10.0%), 369 Normal; scores 0.349-0.758 (mean 0.417). Top-scoring wallets: wallet_350, wallet_146, wallet_152. "Anomalous" = unusual, needs investigator review, not proof of criminality.
6. **Next step:** Forensic/policy rules and evidence on the flagged wallets (awaiting instruction).

## 2026-09-21 – Forensic behavioural rule engine

1. **What changed:** Added `ml/forensic_rules.py` and `data/forensic_results.csv`; README.md and architecture.md updated.
2. **Why:** Isolation Forest only says a wallet is statistically unusual; investigators also need understandable behavioural evidence of why.
3. **Implemented:** Merges features and anomaly results (410 wallets) and applies 11 rules (transaction count, frequency, activity burst, long dormancy, fan-out, fan-in, many counter parties, repeated relationships, received volume, sent volume, incoming/outgoing imbalance). Each has a rule name and a human-readable explanation including the value and threshold. Output columns: rule_count, evidence_level (descriptive, from rule count; not a risk score), triggered_rules (`;`-separated or `none`), explanation. anomaly_score and anomaly_prediction are copied, not recalculated. No feature definitions changed.
4. **Files:** New `ml/forensic_rules.py`, `data/forensic_results.csv`. No new dependencies. Raw CSV, feature CSV, anomaly CSV and anomaly_detection.py unchanged (file hashes identical before and after).
5. **Validation:** 410/410/410 rows, no duplicate wallets or missing values, scores and predictions identical to the originals. 195 wallets trigger 1+ rules, 112 trigger 2+. 40 of the 41 Isolation Forest 'Anomalous' wallets trigger 2+ rules; 215 'Normal' wallets trigger none. Top anomalous wallets (wallet_350, wallet_146, wallet_152) trigger 6 rules each (high transaction count, fan-in, many counterparties, repeated relationships, high volume received, imbalance).
6. **Threshold assumptions:** Strictly above the population 90th percentile (imbalance also below the 10th). Not real-world claims. Caveats: transaction_frequency P90 = 48 is the 1-hour-floor value shared by 61 two-transaction wallets, so strict ">" is needed (8 wallets trigger); activity_burst P90 = 2 (rule means 3+ per hour); imbalance is two-sided (82 wallets).
7. **Next step:** Investigation priority scoring combining anomaly score and rule evidence (awaiting instruction).

## 2026-09-24 – Frontend Phase 1 (investigator workflow)

1. **What changed:** Added `frontend/` (Vite + React + TypeScript). No backend/ML/CSV files were modified.
2. **Why:** Visualise and interact with the existing pipeline outputs: Dashboard -> Anomalies -> Wallet investigation -> Evidence -> Create case.
3. **Implemented:** App shell with sidebar; light/dark themes (CSS tokens, set before first paint); Dashboard (4 metrics, Recharts score histogram and weekly transfer trend, priority queue, ML-vs-forensic matrix); sortable/filterable Anomalies table; wallet investigation page (ML vs forensic evidence, behavioural profile, React Flow counterparty graph); explicit Create Case dialog with local (localStorage) case store; read-only Cases list. Network and Settings are placeholders.
4. **Data flow:** the app imports `data/fusion_results.csv`, `data/wallet_behavior_features.csv` and `dataset/synthetic_bitcoin_transactions.csv` directly (`?raw`, parsed with papaparse); no copies, no API. Scores, rule text and combined_score are shown exactly as the pipeline wrote them; the frontend only counts, ranks and computes population positions.
5. **Dependencies:** react, react-router-dom, recharts, @xyflow/react, papaparse, lucide-react, IBM Plex fonts (all under `frontend/`; nothing global).
6. **Validation:** `tsc --noEmit` and `vite build` pass; pages rendered in Edge in both themes with no console errors; create-case flow verified (case appears, Active cases metric and wallet status update).
7. **Next step:** Phase 2 (network exploration, full Cases module, transaction table).

## 2026-09-24 – Frontend Phase 2 (case detail, transactions/network, settings, export)

1. **What changed:** Extended `frontend/`. No backend, ML, rule, fusion or CSV file was modified.
2. **Why:** Complete the investigator workflow: case review, transaction exploration, configuration and local export.
3. **Implemented:** Clickable cases -> Case Detail (evidence snapshot, notes, history, status change, related transactions, network graph); Transactions / Network page (search and filters, transaction detail, relationship graph, relationship table, path tracing); Settings (theme preference, confirmations, model/data/pipeline information, pipeline output status, export, local data clear); CSV/JSON export for anomalies, transactions, relationships and cases; Dashboard metrics and matrix cells link to filtered views.
4. **Data notes:** The raw CSV has no transaction ID, so rows are shown with timestamp, parties and source row number (not invented IDs). File sizes/modified times and the Isolation Forest settings are read at dev/build time by a small Vite plugin (`virtual:pipeline-info`) because a browser cannot read them.
5. **Dependencies:** none added.
6. **Validation:** `tsc --noEmit` and `vite build` pass; a 46-step browser run (both themes) covering Dashboard -> Anomalies -> Wallet -> Create Case -> Cases -> Case Detail -> Transactions/Network -> Settings -> Export, with counts checked against the raw CSVs; no horizontal overflow at 1440 and 1024 px.
7. **Next step:** awaiting instruction.

## 2026-09-24 – Dataset re-dated to Oct 2025 – Sep 2026

1. **What changed:** `generate_dataset.py` (time window and timestamp placement only); regenerated `dataset/synthetic_bitcoin_transactions.csv`; re-ran `feature_engineering.py`, `anomaly_detection.py`, `forensic_rules.py` and `result_fusion.py`, which rewrote the four CSVs in `data/`.
2. **Why:** The dataset covered only calendar 2025. It should represent about one year of history ending 24 September 2026.
3. **Implemented:** START = 2025-10-01, END = 2026-09-24 23:59:59. Behaviours are still written against the same 365-slot timeline; `random_time()` now maps each slot to a real day using the requested monthly shares (about 8% per month, 9% for Mar and Jun, 10% for Sep) with a random per-day activity level so some days and hours are busier than others. The extra day-level randomness comes from a separate generator (seed + 1), so the main random stream is unchanged: same 410 wallets, wallet ids, amounts, behaviour mix (88.3% normal / 11.7% unusual) and anomaly-generation logic.
4. **Validation:** earliest 2025-10-01 06:17:43, latest 2026-09-24 21:46:39; all 12 months present (7.1%-10.0% each; August is highest because the existing dormant-wallet wake-up pattern sits late in the timeline); 10,000 rows, mirrored, 410 wallets. 13 of 18 features are identical to before (the 5 time-based ones changed). Isolation Forest still flags 41 wallets; 39 of them match the previous run; ML score rank correlation with the previous run 0.991. `ml/test_result_fusion.py` passes.
5. **Note:** Cases saved in a browser before this change refer to the previous dataset; clear them in Settings > Local investigation data if a fresh start is wanted.
6. **Next step:** awaiting instruction.

## 2026-09-25 – Backend + database (Checkpoint 2 of the local-platform upgrade)

1. **What changed:** Added `backend/` (FastAPI + SQLAlchemy + SQLite), `requirements.txt`, `requirements-dev.txt`, `.env.example`, `pytest.ini`; `.gitignore` now ignores `.env` and `data/*.db`. `ml/`, `generate_dataset.py`, the CSVs and the frontend were not touched. Rollback tag: `sih146-checkpoint-before-backend`.
2. **Why:** First step towards a persistent, API-driven investigator platform that wraps (does not replace) the existing offline pipeline.
3. **Implemented and tested:** SQLite schema (13 tables; only `transactions` and `wallets` are populated so far), CSV importer (collapses the mirrored rows into 5,000 transactions with surrogate IDs `syn-000001...`, idempotent, `--replace` after regenerating the dataset), validated ingestion core, transaction/wallet/import/health/overview endpoints, uniform JSON errors, CLI (`python -m backend.cli init-db | import-csv | stats`), server (`python -m backend`).
4. **Not yet implemented (schema only):** network observations, analysis results, leads, clusters, cases; monitoring; real-data source.
5. **Dependencies:** fastapi, uvicorn, sqlalchemy, networkx (later), pytest + httpx (tests), all installed in `.venv` only.
6. **Validation:** 90 new backend tests + the existing 11 fusion tests pass; per-wallet transaction counts in SQLite match `data/wallet_behavior_features.csv` for all 410 wallets; the four `ml/*.py` CLIs still run with identical results; live server exercised over HTTP.
7. **Note:** on this machine importing pandas takes 5-11 s and the API about 11 s, so server start-up takes roughly 10-15 s.

## 2026-09-25 – Monitoring + analysis integration (Checkpoint 3)

1. **What changed:** Extended `backend/` (new `analysis/` package, monitor, inbox and synthetic-stream sources, leads/analysis/monitor endpoints, `fusion_results` table, 65 new tests). `ml/`, `generate_dataset.py`, the CSVs and the frontend were not touched. Rollback tag: `sih146-checkpoint-before-monitoring` (commit 29ffb4f).
2. **Why:** Turn the one-shot CLI pipeline into a continuously updating service that persists results and produces ranked investigative leads.
3. **Implemented and tested:** the existing `ml/` functions are called unchanged (features rounded to 6 dp exactly as the CLI does); results for all 410 wallets match `data/*.csv` exactly (features, ML scores/predictions, triggered rules, combined scores, rank order). Forensic findings stored per rule (value, threshold, the rule's own sentence). Fusion keeps the existing 0.4/0.6 formula, labelled "Prototype fusion weighting - not statistically validated". Priority bands (top 5% High, next 15% Medium, rest Low) are a labelled prototype setting. Lead = ML-flagged OR High band (42 on the project dataset: 41 + wallet_268). Monitor thread: inbox folder + optional synthetic stream + automatic micro-batch re-analysis; verified live with no manual trigger.
4. **Rules of the design:** analysis is per data source (never mixed); wallets with fewer than 2 transactions are left unscored, never filled in; a failing analysis is not retried until data changes; older runs' detail rows are pruned.
5. **Dependencies:** none added.
6. **Validation:** 166 tests pass (155 backend + 11 existing); the four `ml/*.py` CLIs still run with identical output; live server demo on a scratch database (POST -> automatic re-analysis, inbox file, stream, demo scenario).
7. **Caveats:** a full analysis takes about 10 s on this machine (the first also pays ~15-30 s of scikit-learn/pandas imports), so analysis is micro-batched, not per transaction. Isolation Forest always flags ~10% of wallets (contamination), so a newly anomalous wallet can push another out of the flagged set.

## 2026-09-25 – Network metadata, graph and clustering (Checkpoint 4)

1. **What changed:** Extended `backend/` (synthetic network observations, graph API, entity clusters, 68 new tests) and added the new dataset `data/synthetic_network_observations.csv` (6,489 rows, deterministic, seed 148). No schema change to existing tables; `ml/`, `generate_dataset.py`, the other CSVs and the frontend were not touched. Rollback tag: `sih146-checkpoint-before-graph` (commit 56d1009).
2. **Why:** The problem statement covers transaction plus network metadata, and the investigator needs relationships and related entities, not only per-wallet scores.
3. **Implemented and tested:** SYNTHETIC IP/device/session observations (documentation IP ranges only, one device per wallet, ~15% of wallets share a device, sender side observed 80% / receiver side 50%), stored separately from blockchain data and never attached to real_bitcoin transactions; the observation generator continues each wallet's device for live transactions. Graph API with node types wallet/transaction/ip_observation/device/session and edge types sent_to/received_from/observed_from/associated_with/same_device/same_session. Two labelled cluster methods: shared network observation (33 clusters, 98 wallets on the project data) and transaction communities by Louvain (19 clusters covering all 410 wallets). Cluster ids stay stable as clusters grow, merge or split. Clusters refresh after every analysis and carry a priority summary from the existing leads.
4. **Design notes:** the transfer graph is one connected component (410 wallets), so plain connected components were useless; shared infrastructure and community detection are used instead. Network data is not an input to the Isolation Forest.
5. **Dependencies:** none added (networkx was already installed for this).
6. **Validation:** 223 backend tests + 11 existing pass; the four `ml/*.py` CLIs still give identical results; live server demo on a scratch database.
7. **Caveats:** shared devices are random and unrelated to how a wallet is scored; a cluster shows connected activity or shared synthetic infrastructure, never common ownership. Some full-suite runs looked hung for over an hour. Cause (found later, Checkpoint 5): the laptop went to sleep while a background job waited (Windows logged sleep 13:32:38 -> resume 14:49:13, matching a single 4,604 s test setup); it was not a hang. The suite takes about 6 minutes awake. `pytest.ini` still dumps thread stacks if a test genuinely stalls for 5 minutes.

## 2026-09-25 – Case management and search (Checkpoint 5)

1. **What changed:** Extended `backend/` (cases, evidence snapshots, history, notes, wallet review state, unified search, 74 new tests). One additive table (`wallet_reviews`); no change to existing tables. `ml/`, `generate_dataset.py`, the CSVs and the frontend were not touched. Rollback tag: `sih146-checkpoint-before-cases` (commit 8ecd484).
2. **Why:** The investigator must decide what becomes a case, keep the evidence and history of that decision, and find any entity quickly.
3. **Implemented and tested:** `POST/GET/PATCH /api/cases`, items (lead, wallet, transaction, cluster) with the evidence saved at the moment they are added and today's values shown next to it, notes, evidence-reviewed entries, full history (case_created, *_added, item_removed, note_added, evidence_reviewed, status/priority/assignment/title/description changes), related transactions and graph of a case, `PUT /api/wallets/{a}/review` (Under Review marker; Case Created is derived from case membership), `GET /api/search` over wallets, transactions, clusters, IP/observation IDs, devices, sessions and cases. Leads, wallet analysis and clusters now report `case_ids`; overview reports `cases_total` and `active_cases`.
4. **Design rules:** nothing creates a case automatically (verified after a full analysis run); creation is all-or-nothing; cases survive replacing the synthetic data and show `exists: false` for items that no longer exist; search text is matched literally.
5. **Dependencies:** none added.
6. **Validation:** full suite 296 passed + 1 failure found and fixed (history order of a multi-field PATCH depended on set order; now fixed), then all 45 case tests pass; four `ml/*.py` CLIs unchanged; live server demo including a restart (the case, its history and the lead's review status persisted).
7. **Note on slow runs:** see Checkpoint 4 caveat, which was wrong about the cause: the laptop slept during background runs.

## 2026-09-25 – Frontend integration (Checkpoint 6)

1. **What changed:** The existing screens now read the local backend (`/api`, proxied by Vite to port 8000) and keep working from the bundled CSVs when it is not running. Two small backend additions: `GET /api/analysis/wallets` (every scored wallet with evidence, features, lead state, cases and review status in one response) and persisted monitor settings (`GET/PUT /api/settings`, additive table `app_settings`). 12 new backend tests. `ml/`, `generate_dataset.py`, the CSVs, README.md and architecture.md were not touched. Rollback tag: `sih146-checkpoint-before-frontend` (commit 6ab8ca1).
2. **Why:** Leads, clusters, cases, search and monitoring exist only in the backend; the investigator needs them in the UI they already know.
3. **Structure kept:** same routes and same five sidebar items. Leads live inside Anomalies (default view when the backend is connected: Leads / ML-flagged / Not flagged / All, priority filter, expandable "why this is a lead", Create case). Clusters and infrastructure entities live inside Transactions / Network as a third tab, and as "Related entities" on wallet pages. Global search sits in the sidebar (backend only).
4. **Data source:** the data provider tries the backend first. If it is unreachable or holds no analysis, the app falls back to the bundled CSVs (read-only, banner explains why); an empty backend offers "Load synthetic demo data" (import + network + analysis, ~30 s). Screens refresh by themselves when the monitor finishes a newer analysis run.
5. **Cases:** stored in the backend database. Cases saved earlier in the browser (localStorage `sih146.investigation.v1`) were test data; the user asked that they be ignored, so they are not read, shown, migrated or deleted.
6. **Settings:** monitoring on/off, interval, automatic analysis, synthetic stream on/off and rate are applied immediately and saved; the real-Bitcoin source is shown as not available. No credentials are stored or shown anywhere.
7. **Dependencies:** none added.
8. **Validation:** `tsc` and `npm run build` clean; browser test (Edge/Playwright) of the empty-backend flow, lead queue, case creation with a cluster, evidence/notes/status/history, both graphs with and without synthetic infrastructure, clusters, entity page, global search (wallet, transaction, IP, device, case), transaction detail with synthetic observations, settings save and rejection of an invalid value, light and dark themes, and the offline fallback with the backend stopped.
9. **Caveats:** the layout is desktop/tablet (the existing shell has a fixed 236 px sidebar; phone widths overflow, as before); graphs are capped (case/wallet infrastructure graphs at 20 nodes per type) and say when they are truncated; the dashboard's "new leads" counts every lead in the first run.

## 2026-09-25 – Real Bitcoin source (Checkpoint 7)

1. **What changed:** Added the optional real-data adapter (`backend/ingestion/real_bitcoin.py`, `services/real_source.py`, `routers/sources.py`), configuration keys in `.env.example`, `python -m backend.cli fetch-real` and `analyze --source`, `POST /api/analysis/run?source=`, and a read-only "Real Bitcoin source" panel in Settings. `ml/`, `generate_dataset.py`, the CSVs, README.md and architecture.md were not touched. Rollback tag: `sih146-checkpoint-before-real-data` (commit 511fdbd).
2. **Design:** `TransactionSource` already had `SyntheticCSVSource` and `SyntheticStreamSource`; `RealBitcoinSource` is the third implementation and produces the same normalized `SourcedTransaction`. It reads recent confirmed blocks from an Esplora-compatible API (default https://blockstream.info/api, read-only GETs, standard library only, no new dependency). It is OFF unless `SIH146_REAL_BITCOIN_ENABLED=true`, and even then nothing is fetched until a fetch is requested; the monitor never uses it. The optional API key is read from the environment, sent only as a Bearer header, and never returned or logged. All-or-nothing: an API failure stores nothing and invents nothing.
3. **UTXO -> internal model (documented simplification):** sender = the input address with the largest input value; receivers = output addresses that are not also inputs (change and OP_RETURN dropped); one transfer per receiver; real input/output counts kept. This is not entity attribution. Coinbase, unconfirmed, consolidation and address-less transactions are skipped and counted.
4. **Separation:** real rows carry `source = real_bitcoin`, an address cannot belong to both sources, real transactions never receive synthetic IP/device/session observations, real analysis runs are separate, and the synthetic overview, dashboards and transaction lists ignore real data.
5. **Feature availability:** all 18 features are computed from sender/receiver/amount/timestamp, so all can be computed for real transfers, but 4 (time_since_previous_tx, avg_transaction_interval, transaction_frequency, dormancy_duration) are missing for a wallet with a single transaction (verified against the real feature code); such wallets are left unscored, never filled in. IP, device and session are unavailable for real data.
6. **Live check (one block, public API):** block 968554, 150 transactions read with 8 requests -> 269 transfers, 384 wallets; analysis scored 72 wallets (312 too little activity), 8 flagged, 115 transaction-community clusters, 0 network observations, nothing synthetic touched. A first attempt had timed out on a transient network error; the client now retries transient errors with backoff.
7. **Caveats:** a slice of one block is not a sample of the network; all transactions in a block share the block timestamp, so time-based features are degenerate unless several blocks are read; the model flags ~10% of any population by construction; the frontend shows synthetic data only (real data is reachable through the API and CLI).
