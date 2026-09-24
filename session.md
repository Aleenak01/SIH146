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
