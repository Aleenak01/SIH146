# Architecture

**Project:** SIH146 — AI Powered Monitoring and Analysis of Bitcoin Transactions

Keep this file in sync with the code. Status words: **Implemented** (built and tested), **Optional**
(built, off by default), **Not built** (not present). It describes a local prototype on **synthetic** data.

## Purpose and principle
A local, offline-first platform that analyses synthetic Bitcoin transaction and network metadata,
identifies anomalous patterns, prioritises investigative leads and presents explainable findings to an
investigator. An anomaly is an investigative signal for review, **not** proof of criminal or
fraudulent activity. A lead is not a case: only an investigator creates cases.

## Implementation status
| Component | Status |
|---|---|
| Python virtual environment, pandas/numpy/scikit-learn | **Implemented** |
| Synthetic raw dataset generator (`generate_dataset.py`) and CSV | **Implemented** |
| Feature engineering (`ml/feature_engineering.py`) and `data/wallet_behavior_features.csv` | **Implemented** |
| Isolation Forest (`ml/anomaly_detection.py`) and `data/anomaly_results.csv` | **Implemented** |
| Forensic behavioural rules (`ml/forensic_rules.py`) and `data/forensic_results.csv` | **Implemented** |
| Result fusion (`ml/result_fusion.py`) and `data/fusion_results.csv` | **Implemented** (prototype weights, not validated) |
| Backend API (FastAPI, 50 endpoints) and SQLite database (16 tables) | **Implemented** |
| Ingestion: synthetic CSV, synthetic stream, inbox folder, API | **Implemented** |
| Continuous monitoring with automatic micro-batch analysis | **Implemented** |
| Synthetic network metadata (IP / device / session observations) | **Implemented** |
| Investigative leads and priority ranking | **Implemented** (prototype bands) |
| Related-entity clustering (shared observation, Louvain communities) | **Implemented** |
| Relationship graph (API and React Flow UI) | **Implemented** |
| Unified search | **Implemented** |
| Case management (saved evidence, notes, history) | **Implemented** |
| React investigator UI (with CSV fallback when the backend is down) | **Implemented** |
| Real Bitcoin source (Esplora-compatible, read-only) | **Optional**: fully implemented adapter, off by default, not shown in the UI |
| Cytoscape.js graph | **Not built**: React Flow is used instead |
| Authentication, multi-user, deployment | **Not built** (single-user local prototype) |
| Transactions as nodes in the *default* graph view | Available through the graph API (`node_types`), not the default UI view |

## System overview
```
                      ┌──────────────────────────── backend (FastAPI, 127.0.0.1:8000) ────────────────────────────┐
 sources              │                                                                                             │
 ─ synthetic CSV ───┐ │  ingest ──► SQLite (data/sih146.db) ──► analysis bridge ──► leads, priority, clusters      │
 ─ synthetic stream ┼─┼─► validate, dedupe, source label        (calls ml/ unchanged)     │                         │
 ─ inbox folder     │ │        ▲                                                          ▼                         │
 ─ POST /api        ┘ │        │                     cases, review state, search, graph, settings ◄── REST API ─────┼──► browser UI
 ─ real Bitcoin (optional, off) │  monitor thread: inbox + stream + automatic micro-batch re-analysis                 │   (Vite dev server proxies /api)
                      └─────────────────────────────────────────────────────────────────────────────────────────────┘
 ml/ (feature engineering, Isolation Forest, forensic rules, result fusion) is unchanged and still runs on its own from the command line.
```
Design rule: the platform wraps the existing pipeline; it never replaces or edits `ml/`. The backend calls the
same functions in memory and was verified to reproduce the CSV outputs for all 410 wallets (features rounded to
6 decimals exactly as the CLI, scores, predictions, rules, combined result, rank order).

## Implemented: raw dataset generation
`generate_dataset.py` (Python, pandas + numpy, seed 42) writes
`dataset/synthetic_bitcoin_transactions.csv` and validates it.

**Current raw schema (exactly 7 columns):** `timestamp`, `wallet_address`, `amount_btc`,
`direction` (Incoming/Outgoing), `input_count`, `output_count`, `counterparty_wallet`.
The current dataset contains **no** IP/network metadata, transaction IDs, fees, labels or scores.
Do not change the schema without agreement.

**Data model:** each transfer A → B is written as two mirrored rows (A Outgoing with counterparty B;
B Incoming with counterparty A) sharing timestamp, amount and input/output counts. Rows are sorted by
timestamp. Wallets are synthetic ids (`wallet_001` …), shuffled so ids reveal no role. Result:
10,000 rows from 5,000 transfers across 410 wallets.

**Generation stages:**
1. Base network: 320 wallets in communities of 16, each with its own typical amount, activity level
   and usual partners.
2. Unusual patterns: rapid fund movement, dormant activation, high-frequency bursts, fan-out,
   fan-in, repeated relationships, unusual amounts.
3. Normal activity: merchant-like hubs, payroll-like repeats, slow multi-hop chains, background
   transfers between usual partners.
4. Rows written, then validated from disk (schema, missing values, directions, amounts, counts,
   mirror consistency, repeated pairs, multi-hop paths).

Behaviour tags exist only in memory for the validation report (about 88% normal / 12% unusual) and
are not written to the CSV.

**Environment:** `.venv` (Python 3.14.5, pandas 3.0.6, numpy 2.5.3); VS Code configured via
`.vscode/settings.json`.

## Implemented: feature engineering
`ml/feature_engineering.py` reads the raw CSV (`data/` if present, else `dataset/`), checks it
(schema, no missing values, Outgoing/Incoming rows mirrored so no wallet side is lost), and writes
`data/wallet_behavior_features.csv`: one row per wallet (410) with `wallet_address` plus 18 features.
Time gaps are in hours, frequency is per day. Features describe each wallet's full observed history.

| Group | Features |
|---|---|
| Transaction behaviour | transaction_count, incoming_count, outgoing_count, total_received_btc, total_sent_btc, avg_transaction_amount |
| Time behaviour | time_since_previous_tx, avg_transaction_interval, transaction_frequency, dormancy_duration, activity_burst |
| Flow behaviour | incoming_outgoing_ratio, fan_in, fan_out |
| Network / relationship | unique_counterparties, wallet_degree, repeated_connections, hop_distance |

Key definitions:
- **activity_burst:** largest number of a wallet's transactions inside any 1-hour window.
- **transaction_frequency:** transactions per day over the wallet's observed period (period floored at 1 hour).
- **dormancy_duration:** longest gap between consecutive transactions (hours).
- **incoming_outgoing_ratio:** received / sent, capped at 100; wallets that never sent get 100.
- **wallet_degree:** distinct directed links (fan_in + fan_out); differs from unique_counterparties
  (direction ignored), since a partner that both sends and receives counts twice.
- **hop_distance (simplified):** mean shortest-path hops (undirected, ignoring time) from the wallet
  to every reachable wallet; lower = more central. Does not follow time-ordered fund paths.
- Wallets with fewer than 2 transactions would get NaN time features (none in the current synthetic data; the backend leaves such wallets unscored, which matters for real data).

## Implemented: Isolation Forest anomaly detection
`ml/anomaly_detection.py` loads `data/wallet_behavior_features.csv`, checks the columns, numeric
types and absence of NaN/infinite values (it stops rather than dropping wallets), and fits
scikit-learn `IsolationForest` on the 18 numeric features. `wallet_address` is not given to the
model, and there are no labels: it is unsupervised.

- **Configuration:** `n_estimators=200`, `contamination=0.10`, `random_state=42`. No feature scaling
  (Isolation Forest splits within each feature's own range, so scale does not matter).
- **Contamination 0.10:** a prototype assumption for the review-queue size (~41 of 410 wallets),
  consistent with the 10-15% unusual activity aimed for in the synthetic data. It only sets the
  threshold; scores do not depend on it.
- **Output `data/anomaly_results.csv`:** `wallet_address`, `anomaly_score`, `anomaly_prediction`.
- **anomaly_score = -score_samples(X):** higher = more unusual (roughly 0-1; near 0.5 or below is
  typical). It is relative to this wallet population; not a probability and not a risk score.
- **anomaly_prediction:** `Anomalous` if flagged by the model's threshold, else `Normal`.
- **Interpretation:** "Anomalous" means unusual behaviour, an investigation lead requiring
  investigator review. It is not evidence of criminal or fraudulent activity.

## Implemented: forensic behavioural rules
`ml/forensic_rules.py` merges `data/wallet_behavior_features.csv` and `data/anomaly_results.csv` on
`wallet_address` (410 wallets, none dropped) and answers "why might this wallet deserve investigator
attention?". It reads the existing features with their existing definitions and copies `anomaly_score` /
`anomaly_prediction` unchanged (verified). Output: `data/forensic_results.csv` with `wallet_address`,
`anomaly_score`, `anomaly_prediction`, `rule_count`, `evidence_level`, `triggered_rules`
(`;`-separated, or `none`), `explanation` (human-readable, includes the value and threshold).

Thresholds are relative to this synthetic population: a "high" rule triggers when a value is strictly
above the 90th percentile.

| Rule | Feature |
|---|---|
| high_transaction_count | transaction_count |
| high_transaction_frequency | transaction_frequency |
| activity_burst | activity_burst |
| long_dormancy (long inactivity period) | dormancy_duration |
| fan_out | fan_out |
| fan_in | fan_in |
| many_counterparties | unique_counterparties |
| repeated_relationships | repeated_connections |
| high_received_volume | total_received_btc |
| high_sent_volume | total_sent_btc |
| incoming_outgoing_imbalance | incoming_outgoing_ratio: above 90th percentile (mostly received) or below 10th percentile (mostly sent) |

`evidence_level` only describes how many indicators triggered: 0 = No specific rule triggered,
1 = Single behavioural indicator, 2-3 = Multiple behavioural indicators, 4+ = Multiple strong
behavioural indicators. It is **not** a risk score. Rule triggers are behavioural indicators of
unusual behaviour, not proof of criminal or fraudulent activity; leads require investigator review.

Known threshold caveats (feature definitions were not changed):
- `transaction_frequency` 90th percentile is exactly 48: 61 wallets with only 2 transactions inside
  one hour all get 48/day from the 1-hour period floor. The strict "above" test skips them, so the rule
  triggers for 8 wallets that have 13+ transactions.
- `activity_burst` 90th percentile is 2, so the rule means 3+ transactions in one hour.
- `incoming_outgoing_imbalance` is two-sided, so it triggers for more wallets (82) than other rules.
- The rules are independent 90th-percentile tests, so about half of all wallets trigger at least one.

## Implemented: result fusion
`ml/result_fusion.py` reads `data/forensic_results.csv` and `data/anomaly_results.csv` (neither is modified) and writes
`data/fusion_results.csv` (per wallet: forensic score/rule count/evidence level/rules/findings, ML score/prediction,
`combined_score`, weights, `valid`, `warnings`, `errors`).
- `forensic_score = rule_count / 11` (there are 11 rules); `ml_score` is the anomaly score as is (validated to lie in [0, 1]).
- `combined_score = 0.40 × forensic_score + 0.60 × ml_score`. **Prototype fusion weighting — not statistically validated.**
- A wallet with missing or out-of-range evidence is marked invalid with the reason, never defaulted.

## Implemented: backend and database
`python -m backend` (FastAPI + uvicorn) with SQLAlchemy over SQLite (WAL, foreign keys on). Nothing needs the internet.
Layout: `backend/ingestion` (sources), `backend/analysis` (bridge, fusion/priority, clustering), `backend/services`
(ingest, monitor, leads, graph, cases, search, settings, real source), `backend/routers` (endpoints), `backend/tests`.

**Tables (16):** `transactions`, `wallets` (both carry `source` = `synthetic` or `real_bitcoin`), `network_observations`
(synthetic), `analysis_runs`, `wallet_features`, `anomaly_results`, `fusion_results`, `forensic_findings`,
`investigative_leads`, `entity_clusters`, `entity_cluster_members`, `wallet_reviews`, `cases`, `case_items`,
`case_history`, `app_settings`. Tables are created additively; existing data is never dropped by start-up.

**Sources and normalization.** Every source implements `TransactionSource` and yields the same normalized object
(`SourcedTransaction`, wrapping `TransactionIn`: id, UTC time, sender, receiver, amount ≥ 1 satoshi, input/output counts).
`SyntheticCSVSource` collapses the two mirrored rows of each transfer into one transaction and assigns deterministic
surrogate ids `syn-000001…` (the raw CSV has no transaction id). The synthetic stream, the inbox (`.csv` / `.jsonl`
files) and `POST /api/transactions` feed the same ingestion function, which validates, de-duplicates and reports
rejected items individually. A wallet can belong to only one source.

**Analysis bridge.** `run_analysis(source)` loads that source's transfers, rebuilds the raw layout the CLI expects,
calls `ml/` unchanged, and stores features, ML results, per-rule findings (value, threshold, the rule's own sentence),
fusion, priority and leads. Analysis is per source and never mixed. Wallets with fewer than two transactions have no time
features and are left unscored (reported, never filled in); at least 20 scoreable wallets are required. Only one analysis
runs at a time.

**Priority and leads (prototype settings, not validated).** `priority_rank` orders wallets by combined score;
`priority_level` = High (top 5%), Medium (next 15%), Low (rest). A wallet is an investigative lead if the Isolation
Forest flags it **or** it is High (42 leads on the project data: 41 flagged plus one High-band wallet). Each lead stores
human-readable reasons and the label "Prototype fusion weighting — not statistically validated."

**Monitoring.** A thread ticks every `SIH146_MONITOR_INTERVAL` seconds (default 5): inbox → optional synthetic stream →
automatic re-analysis if new transactions arrived since the last completed run. It is micro-batch (the model and percentile
rules are population-relative and a run takes seconds). A failing analysis is not retried until the data changes. The
reported source is always "Synthetic dataset" or "Synthetic stream", with `is_real_data: false`. An optional, labelled
demo scenario (a quiet wallet fans out to many new wallets) can be injected.

**Synthetic network metadata.** `data/synthetic_network_observations.csv` (6,489 rows, seed 148) attaches a synthetic IP
(documentation ranges 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 only), device, session, user agent and region to
transactions (sender side 80%, receiver side 50%; ~15% of wallets share a device). Observation ids are `obs-<tx>-S|R`.
They are never inputs to the model, never attached to real data, and always labelled synthetic.

**Clustering (refreshed after every analysis; ids are stable as clusters grow).** Two labelled methods:
`shared_network_observation` (wallets sharing a synthetic IP/device/session; `NET-…`) and `transaction_community`
(Louvain on the transfer graph; `TXC-…`). The transfer graph is one connected component, so plain connected components
were useless. Each cluster carries a priority summary from the leads. A cluster shows connected activity, never ownership.

**Graph API.** `GET /api/graph` with one focus (wallet, transaction, cluster, or the current leads): node types wallet,
transaction, ip_observation, device, session; edge types sent_to, received_from, observed_from, associated_with,
same_device, same_session; depth, node and transaction caps, and a `truncated` flag with what was omitted.

**Cases.** Created only by an investigator. A case holds items (lead, wallet, transaction, cluster); each item stores the
evidence as it was when added, and the case detail shows today's values next to it. Notes, evidence-reviewed entries and
every change (status, priority, assignment, title, items) go into the history. Wallet "Under Review" is a marker; "Case
Created" is derived from case membership. Cases survive re-analysis and restarts.

**Search.** `GET /api/search` (min 2 characters, literal matching, exact matches first) over wallets, transactions, clusters,
IP addresses/observation ids, devices, sessions and cases.

**Settings.** Monitor on/off, interval, automatic analysis and the synthetic stream are saved in `app_settings` and applied at
start-up. The real source is configured only through environment variables. No credentials are stored by or returned from the API.

**Errors and safety.** Uniform error body `{"error": {"code", "message", "details"?}}`; parameterized SQL; text search is
escaped; the server binds to 127.0.0.1; CORS is limited to the local dev origins; `.env` and the database are git-ignored.

## Optional: real Bitcoin source
`backend/ingestion/real_bitcoin.py` (`RealBitcoinSource`, `EsploraClient`) reads recent confirmed blocks from an
Esplora-compatible API. **Off** unless `SIH146_REAL_BITCOIN_ENABLED=true`; even then it only runs when a fetch is requested
(`POST /api/sources/real-bitcoin/fetch` or `backend.cli fetch-real`), never from the monitor. Standard library only, read-only
GETs, request delay, retries with backoff for rate limits and transient errors, size caps, an optional Bearer key that is
never returned or logged. All-or-nothing: on any API failure nothing is stored and nothing is invented.

UTXO → internal model (a documented simplification, not entity attribution): sender = the input address with the largest
input value; receivers = output addresses that are not also inputs (change and OP_RETURN dropped); one transfer per receiver
with the real input/output counts; coinbase, unconfirmed and consolidation-only transactions are skipped and counted.

Separation and availability: real rows are `source = real_bitcoin`; real analysis is a separate run
(`POST /api/analysis/run?source=real_bitcoin`); real data never receives synthetic observations and does not appear in the
synthetic overview, dashboards or transaction lists. All 18 features are computed from sender/receiver/amount/time, so all can
be computed for real transfers, but four (`time_since_previous_tx`, `avg_transaction_interval`, `transaction_frequency`,
`dormancy_duration`) do not exist for a wallet with one transaction; such wallets are unscored. IP, device and session are
unavailable for real data. Verified live on one public block (150 transactions → 269 transfers, 72 of 384 wallets scoreable).

## Implemented: frontend
`frontend/` (Vite + React + TypeScript, Recharts, React Flow, react-router). Sidebar: Dashboard, Anomalies, Cases,
Transactions / Network, Settings; light and dark themes authored separately.
- **Data provider:** tries the backend; falls back to the bundled CSVs (read-only, banner explains) if it is unreachable or
  empty; offers to load the demo data into an empty backend; reloads when the monitor completes a newer run.
- **Dashboard:** counts, monitoring strip (source label, last transaction, last analysis, state, new leads).
- **Anomalies:** the lead queue (default with the backend), ML-flagged / not flagged / all, priority filter, expandable reasons, Create case.
- **Wallet:** evidence, behavioural profile against the population, counterparty graph, related entities, case actions.
- **Cases:** list, detail with saved evidence and today's values, notes, history, graph (optional synthetic infrastructure), related transactions, export.
- **Transactions / Network:** transactions with details and synthetic observations; network explorer and path trace; **Clusters** tab and synthetic entity pages.
- **Search** in the sidebar; **Settings** for monitoring, the real-source status (read-only), confirmations and exports.

## Not built / future
- Authentication and multi-user roles; production deployment.
- Real-time (per-transaction) analysis and stream processing.
- Supervised models or validated fusion weights (needs labelled, investigator-reviewed outcomes).
- Showing real Bitcoin data in the UI, address attribution or co-spend clustering, mempool monitoring.
- Cytoscape.js (React Flow replaced it).

## Deviations from the original plan
- The planned Cytoscape.js graph was built with React Flow.
- "Investigative Leads" and "Data Ingestion" are not separate top-level screens: leads are inside Anomalies, ingestion is driven by
  the monitor and Settings, keeping the original five-item navigation.
- Risk/confidence is expressed as the prototype combined result and priority band, not a calibrated probability.
