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
| Backend API (FastAPI, 61 endpoints) and SQLite database (28 tables) | **Implemented** |
| Common-input-ownership address entities + network correlation (Phase 2, synthetic) | **Implemented** (no UI yet; separate from the wallet-level pipeline) |
| Ingestion: synthetic CSV, synthetic stream, inbox folder, API | **Implemented** |
| Rich transaction model, offline GeoIP and CSV/JSON/JSONL/XML ingestion (Phase 1) | **Implemented** (synthetic; not used by the analysis) |
| Peeling-chain, CoinJoin-like detection and on-demand risk propagation (Phase 3 Part A) | **Implemented** (heuristic signals; no UI yet) |
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

**Tables (20):** `transactions`, `wallets` (both carry `source` = `synthetic` or `real_bitcoin`), `network_observations`
(synthetic), `analysis_runs`, `wallet_features`, `anomaly_results`, `fusion_results`, `forensic_findings`,
`investigative_leads`, `entity_clusters`, `entity_cluster_members`, `wallet_reviews`, `cases`, `case_items`,
`case_history`, `app_settings`, and the four rich-model tables `tx_details`, `tx_inputs`, `tx_outputs`, `flow_records` (see below). Tables are created
additively; existing data is never dropped by start-up.

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

## Rich transaction model (Phase 1)
Status: **Implemented** (data model, GeoIP, four-format ingestion, enrichment import). Everything in it is **synthetic**. It is stored beside the
existing model and is not read by `ml/` or by the analysis; no result changed (410 wallets, 41 flagged, 42 leads, 52 clusters, and the pipeline CSVs are
value-identical, also after importing the whole rich dataset).

**New tables (additive; no existing table or column changed):** `tx_details` (one row per detailed transaction: unique `txid`, fee, script type),
`tx_inputs` and `tx_outputs` (address, amount, position), `flow_records` (synthetic network flow: `src_ip`, `dst_ip`, ports, `geo_country`, `asn`,
`asn_org`, `is_synthetic`, `origin = synthetic_flow_record`). All reference `transactions.transaction_id`; indexes on txid, address, src_ip, dst_ip, asn.
Replacing the synthetic data (`purge_source`) removes these rows first, so that flow keeps working.

**Rich dataset** (`generate_rich_dataset.py`, seed 149, output in `dataset/rich/`): one record per existing transfer (5,000), same order and same
`syn-` ids. Opaque synthetic addresses (1,284, each wallet owns 1-4), exact value balance in satoshis (inputs = outputs + fee), a payment to the
receiver plus change to unspent addresses of the sender, 17% of transactions spending 2-3 different addresses of one sender, a synthetic network flow
(client IPs from a pool per wallet, 15% of wallets sharing an IP, 8 node IPs, ephemeral source ports, destination port 8333 in 90%), and country / ASN
from a real GeoIP lookup. Flattening every record to (timestamp, sender wallet, receiver wallet, `amount_btc`, input count, output count) reproduces the
original 5,000 transfers exactly. `sender_wallet`, `receiver_wallet`, `amount_btc` and `ground_truth_address_owner.csv` exist only for that flat view and
for validation: **later detectors must not use them**. IP addresses are randomly assigned synthetic values sampled from public ranges; they are not
observed traffic and no real person or network did anything.

**GeoIP** (`backend/services/geoip.py`): offline lookups from the DB-IP Lite country and ASN databases in `data/geoip/` (MMDB, `maxminddb`). Opened
lazily and cached; if the files are missing or unreadable, lookups return empty values and `GET /api/geoip/status` says so. Private, reserved and
documentation addresses are never looked up. **IP geolocation by DB-IP.com** (https://db-ip.com), licensed CC BY 4.0; see `data/geoip/ATTRIBUTION.txt`.

**Formats** (`backend/ingestion/rich_formats.py`): CSV (list fields as JSON arrays in the cells), JSON (array or `{"transactions": [...]}`), JSONL and XML
(`<transactions><transaction>...`, parsed with `defusedxml`: DTDs, entities and external references are rejected). Every record is validated by one model:
64-hex `txid`, UTC timestamp, fee >= 0, script type in the allowed set, non-empty address and amount lists of equal length, amounts >= 1 satoshi, value
balance within 1 satoshi, valid IPs, ports 0-65535. Bodies are limited to 30 MB and 20,000 records.

**Enrichment import** (`backend/services/rich_import.py`; CLI `python -m backend.cli import-rich <file> [--format ...]`, API `POST /api/import/rich?format=...`):
a record that names an existing `legacy_transaction_id` gets its detail rows attached (the flat transaction is never duplicated); a record without one is first
created through the existing ingest function (source `synthetic`, without legacy network observations) and then detailed. De-duplicated by `txid`, so
re-importing changes nothing; a txid that returns with different content is rejected, not overwritten; a record that contradicts the stored transaction
(sender, receiver, amount, timestamp) is rejected. Like `/api/transactions/batch`: each record is checked on its own and rejected ones are reported by
index, the valid ones are saved together in one database transaction. Missing geo fields are filled from the lookup; provided ones are kept and any
difference from the lookup is reported as a warning. Read endpoints: `GET /api/transactions/{id}/details`, `GET /api/geoip/status`.

Not built in this phase: address-level correlation or entity graph, common-input-ownership clustering, peeling-chain / CoinJoin / risk detectors, a
confidence score, transaction-level anomaly scoring, UI for any of it, and changes to the monitor or inbox.

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
- **Wallet:** evidence, behavioural profile against the population, counterparty graph, related entities (clusters, address entity, peeling chain membership), on-demand risk propagation, case actions.
- **Cases:** list, detail with saved evidence and today's values, notes, history, graph (optional synthetic infrastructure), related transactions (with CoinJoin badges), export; items can be a lead, wallet, transaction, cluster or address entity.
- **Transactions / Network:** transactions with details and synthetic observations; network explorer and path trace; **Clusters** tab and synthetic entity pages; **Entities** tab (common-input-ownership address entities, Phase 2/3 Part B); CoinJoin badge on transaction rows.
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

## Rules for later phases
These rules carry the design decisions of Phase 1 forward. Anything built on the rich data (entities, correlation, detectors) must follow them.

1. **Leak fields.** `sender_wallet`, `receiver_wallet` and `amount_btc` exist in rich records only to build the flat view for the old wallet-level
   pipeline. Entity, correlation and detector code must **not** read them (they name the true owner and the true payment). The ground-truth file
   `dataset/rich/ground_truth_address_owner.csv` is for offline validation scripts and tests only, never for backend analysis. A test enforces this for
   the analysis code that exists today, and new modules need the same test.
2. **Two separate synthetic network layers exist and are not linked.**
   - *Legacy layer*: per-wallet `network_observations` (IP, device, session, region; ids `obs-<tx>-S|R`). Used by the `NET-` clusters, `GET /api/network/observations`,
     `GET /api/network/entities/...`, the infrastructure nodes of `GET /api/graph` and of the case graph, search (IPs, devices, sessions), and the UI (Related entities,
     the Clusters tab, the transaction detail panel, the case graph option).
   - *Flow layer* (Phase 1): `flow_records`, one row per transaction (source IP, destination node IP, ports, country, ASN, tied to the txid). Today only
     `GET /api/transactions/{id}/details` returns it; no screen shows it.
   - New correlation is built on `flow_records`. The legacy layer and its clusters are left as they are. Both layers are synthetic, and neither is observed traffic.
3. **Wallet level versus address level.** The old pipeline (features, Isolation Forest, rules, fusion, leads, `TXC-`/`NET-` clusters, cases) is wallet-level. The new entity layer is
   address-level: addresses grouped into entities. An entity is **not** the same object as a wallet or a wallet cluster and must not be presented as one.
4. **Heuristics are indications, not proof.** Co-spending inputs (common-input ownership) and similar rules indicate *likely* common control, never proof. CoinJoin-like transactions
   can break the co-spend heuristic, so the later CoinJoin work must be able to exclude such transactions from entity building.
5. **Reproducibility of the rich dataset.** The committed files in `dataset/rich/` are the reference. Regenerating with a newer DB-IP monthly release gives a different dataset (different IP
   ranges). Git may convert line endings on Windows, so byte hashes of the `dataset/rich/` files can differ after a fresh clone; parsing is unaffected.

## Entity layer (Phase 2)
Built entirely on the Phase 1 rich address-level tables (`tx_details`, `tx_inputs`, `tx_outputs`, `flow_records`).
Additive: nothing above is touched, and this layer is a separate step, not part of the wallet-level analysis run
or the monitor.

**Common-input-ownership entities** (`backend/analysis/entities.py`). The classic Bitcoin-forensics heuristic:
addresses spent together as inputs of one transaction are very likely controlled by the same wallet/person
(union-find over `tx_inputs`, grouped by `transaction_id`). An address that never co-spends with another stays its
own singleton entity. Entity ids are stable across re-runs (`CIO-<lowest address>`, reassigned by largest address
overlap when entities merge, the same approach as the wallet clusters). Reads **only** `tx_inputs`/`tx_outputs`/
`tx_details`/`flow_records`; a test inspects the module's source and fails if it references `sender_wallet`,
`receiver_wallet`, `amount_btc`, or the ground-truth file. An entity is a heuristic grouping -- "likely common
control", never proof -- and is known to be broken by CoinJoin-like transactions (excluding those is future work).

**Network<->blockchain correlation** (`backend/analysis/correlation.py`), linked through the shared `transaction_id`
(and therefore `txid`):
- `entity_ip_links`: an entity spent from an address whose transaction's synthetic `flow_records.src_ip` was this IP
  (transaction count, first/last seen).
- `entity_links`: two entities whose spending transactions were seen from the **same** synthetic `src_ip`
  (`shared_ip`; weight = number of distinct shared IPs).
- `correlation_findings` (evidence, never a verdict, each keeping its own evidence): `ip_used_by_several_entities`,
  `entity_many_countries` (3+), `entity_many_asns` (3+), `entity_unusual_port` (destination port other than 8333).

**Storage (5 new additive tables, table count 20 -> 25):** `address_entities`, `address_entity_members`,
`entity_ip_links`, `entity_links`, `correlation_findings`.

**Run control:** a separate step, not hooked into the wallet-level analysis run or the monitor.
`python -m backend.cli build-entities [--source]` and `POST /api/entities/run`. Idempotent and deterministic;
returns `rich_data_available: false` if no rich data has been imported for that source yet.

**API (4 new endpoints, count 53 -> 57):** `GET /api/entities` (paging; filters `min_addresses`, `country`, `asn`,
`ip`, `q`, and — added in Phase 3 Part B — `wallet`), `GET /api/entities/{id}` (addresses, spending transactions,
IP/ASN/country links, related entities, findings), `GET /api/entity-graph` (focus `entity`/`address`/`ip`/`txid`;
node types `entity`/`address`/`transaction`/`ip`/`asn`/`country`; edge types `in_entity`/`input_of`/`output_to`/
`sent_from_ip`/`in_asn`/`in_country`/`shared_ip`; depth and node caps with a `truncated` flag, same style as
`/api/graph`). This is a separate graph from the wallet-level `/api/graph`; neither reads the other.

**Read-only wallet cross-reference, added Phase 3 Part B** (`entity_queries.linked_wallets_for_entities`): every
`GET /api/entities` and `GET /api/entities/{id}` response now also carries `linked_wallets` — the wallet(s) that
sent a transaction the entity spent from, found by joining `address_entity_members` -> `tx_inputs` ->
`transactions.sender_wallet`. This is presentational only, computed after the entity is built, and is never read
back into the union-find in `analysis/entities.py` (rule 1 above still holds -- checked by the same leak test). The
`wallet` filter on `GET /api/entities` uses the same join, in reverse, to answer "which entities did this wallet's
spending touch".

**Offline validation** (`scripts/validate_entities.py`, **not** part of the backend or the analysis pipeline):
builds its own scratch database, imports the rich dataset, runs the entity build, and compares the result with
`dataset/rich/ground_truth_address_owner.csv` (the true wallet each address belongs to, which only this validation
script reads). It reports entity purity (share of an entity's addresses owned by one true wallet), wallet
completeness (share of a wallet's addresses that ended up in one entity), and how many wallets stay fragmented
because their addresses were never spent together in one transaction -- by design of the heuristic, not a bug.

## Pattern detectors (Phase 3, Part A)
Three detectors on top of the existing wallet-level and rich (address-level) data. Additive: nothing above is
touched, none of this is hooked into the wallet-level analysis run or the monitor, and all three are heuristic
signals -- investigative leads for a human to check, never proof of anything.

**Peeling-chain detection** (`backend/analysis/peeling.py`). A peeling chain is a sequence of wallet-level transfers
A0 -> A1 -> ... -> An where each hop forwards most of what the wallet just received, in one transaction, to the next
wallet (a large balance walked down a chain, "peeling off" a small amount at each stop). Built entirely from the
existing flat `transactions` table (sender/receiver/amount/timestamp) -- no rich data needed. Rule: each wallet is
walked through its own timeline; a receipt is "consumed" by whichever outgoing transfer comes right after it
(qualifying or not), so only the immediate next spend is ever a candidate continuation, and it qualifies if it
forwards at least `SIH146_PEELING_DOMINANCE_SHARE` (default 0.85) of what was received. This makes the predecessor
mapping a disjoint set of simple chains (deterministic, no overlap, no exponential search). A chain is stored once
it reaches `SIH146_PEELING_MIN_HOPS` (default 3) transfers; id `PEEL-<its first transaction's id>`.
Prototype thresholds, not statistically validated -- same status as `fusion.py`'s `HIGH_SHARE`/`MEDIUM_SHARE`, but
overridable via `backend/config.py` / the environment (fusion.py's are not).

**CoinJoin-like detection** (`backend/analysis/coinjoin.py`). A transaction whose rich data shows several distinct
input addresses (`SIH146_COINJOIN_MIN_INPUTS`, default 3) together with several outputs of about the same value
(`SIH146_COINJOIN_MIN_EQUAL_OUTPUTS`, default 3, within `SIH146_COINJOIN_EQUAL_VALUE_TOLERANCE` = 1%) is a candidate.
`score` (0-1) is the share of outputs in the largest equal-value group, scaled down when the input/output counts are
very unbalanced -- a heuristic confidence, not a probability. A candidate flag, never a certainty: ordinary
transactions can occasionally match by chance.

**The Phase 2 fix.** `analysis/entities.py`'s common-input-ownership union-find now excludes any transaction present
in `CoinJoinCandidate` for that source from its input set, so a CoinJoin's pooled inputs are not read as evidence
that its participants are one entity. If CoinJoin detection has never been run (the table is empty), nothing is
excluded and behaviour is unchanged from Phase 2 -- the exclusion is additive, not a prerequisite. On the project's
synthetic rich dataset this changes nothing (0 CoinJoin-like candidates are found there: the Phase 1 generator was
not designed to produce them), verified instead with a crafted scratch dataset: a 5-address CoinJoin-like pool
merged into one entity before the fix, and disappeared entirely after it (no other co-spend evidence remained for
those addresses), while unrelated genuine entities were unaffected (`backend/tests/test_patterns.py`).

**On-demand risk propagation** (`backend/analysis/risk_propagation.py`) -- an investigator tool, not a stored table
or a background job. Given one or more seed wallets (initial score 1.0), a multi-source breadth-first search assigns
every reachable wallet `decay_per_hop ** hop_distance` (default decay 0.5, default max 4 hops, capped at 200 nodes;
all overridable per call or via config). It walks the wallet transfer graph through `wallet_neighbor_links`, a
function extracted from `services/graph.py` (a pure, behaviour-preserving refactor of the loop `build_graph` already
used to expand `GET /api/graph`) so both walk the same graph the same way instead of two independent
implementations; `test_graph.py`'s full suite still passes unchanged. Heuristic investigator aid, not a validated
risk score: it says only how many transfer hops away a wallet is, never that it did anything wrong.

**Storage (3 new additive tables, table count 25 -> 28):** `peeling_chains`, `peeling_chain_hops` (peeling, built
from `transactions`), `coinjoin_candidates` (built from the rich tables).

**Run control:** a separate step, like `build-entities`. `python -m backend.cli detect-patterns [--source]` runs
peeling + CoinJoin together and prints a summary; idempotent (replaces what was stored for that source). Recommended
order: `detect-patterns` before (re-)running `build-entities`, so CoinJoin exclusion takes effect.

**API (4 new endpoints, count 57 -> 61):** `GET /api/peeling-chains` (paging; filter `wallet`; each item's
`wallets` field -- added Phase 3 Part B -- is every wallet appearing in any hop of the chain, not only the two
endpoints, so a UI filter can ask "is this wallet part of *any* peeling chain" in one page fetch instead of one
detail call per chain), `GET /api/peeling-chains/{chain_id}` (its hops in order), `GET /api/coinjoin-candidates`
(paging), `POST /api/risk/propagate` (body: `seed_wallets`, optional `max_hops`/`decay_per_hop` overrides -> ranked
`{wallet, propagated_score, hop_distance, path}`).

## Frontend for entities and pattern detectors (Phase 3, Part B)
Phase 2 shipped with no UI at all, and Phase 3 Part A's three detectors were API-only; Part B gives both a UI,
reusing the existing design system exactly (`Panel`, `PageHeader`, `data-table`, the `seg` tablist + `?view=` query
param, `PriorityPill`/`PredictionText`/`rule-tag`/`chip-link`, the `related-block` pattern, `ApiGraph`). No new CSS
file was needed -- every new panel is built entirely from classes that already existed.

- **Entities tab** (`frontend/src/components/EntitiesView.tsx`), a 4th tab on `/network` (`?view=entities`),
  structured like `ClustersView.tsx`: a filter row (address or entity id), a list table (entity id, address count,
  linked wallets, transaction count), and an `EntityPanel` detail (metrics-3, member addresses, correlation
  findings, an `ApiGraph` mini-graph via `/api/entity-graph`, "Create case from entity"). Its disclaimer
  deliberately says the opposite of the Clusters tab's: an entity **is** an ownership inference (heuristic, not
  proof), where a cluster is connected activity, not ownership.
- **`RelatedEntities.tsx`** (used by Wallet Detail and the Network page) gained two more `related-block`s beside
  its existing Clusters and Network-observations blocks: **Address entity** (`GET /api/entities?wallet=`) and
  **Peeling chain membership** (`GET /api/peeling-chains?wallet=`), the latter expandable per chain to its full hop
  sequence (`GET /api/peeling-chains/{chain_id}`), highlighting this wallet's hop.
- **CoinJoin badge**: `TransactionTable.tsx` takes an optional `coinjoinIds` prop and renders a `possible CoinJoin`
  `rule-tag` next to the transaction id when it is in the set -- same mechanism as the existing `repeated` tag. Both
  callers (`Network.tsx`'s transactions view, `CaseDetail.tsx`) fetch `GET /api/coinjoin-candidates` once and pass
  the ids down.
- **Anomalies filter**: a `Part of a peeling chain` checkbox, same `check`-class pattern as the existing filters,
  matching against the union of every peeling chain's `wallets` field (one `GET /api/peeling-chains?limit=500`
  call, not one per chain).
- **Risk propagation panel** on Wallet Detail: calls `POST /api/risk/propagate` with the current wallet as the sole
  seed on demand (nothing is auto-run or stored), shows the ranked results in a `data-table`.
- **Case item type extended to "entity"** (`backend/schemas.py`'s `ItemType`, `backend/services/cases.py`): the
  same generic case-item mechanism already used for `cluster` (`ADDED_ACTION`, `build_snapshot`, `_current`,
  `_counts`, `graph_seed_wallets`) was extended rather than faking "create case from entity" through a wallet item,
  since an entity has no owning wallet of its own. `graph_seed_wallets` seeds the case graph from the entity's
  `linked_wallets`. `entity_queries.get_entity_detail`'s dict return (unlike the Pydantic-model-backed cluster/lead
  snapshots) needed its datetimes converted by hand before going into the JSON `evidence_snapshot` column --
  `services/cases.py::_json_safe`.

## Wallet insights: confidence score, typology tags, geo aggregation (Phase 4, Part A)
Closes a gap the problem statement asks for directly: leads only carried the raw prototype `combined_score`
(0.4 forensic + 0.6 ML), which is a blend, not a confidence measure, and GeoIP country/ASN data has existed since
Phase 1 with nothing surfacing it. All three pieces here are additive and read-only over what already exists;
nothing above is touched, none of it is hooked into the wallet-level analysis run or the monitor.

**Confidence score** (`backend/analysis/confidence.py`) -- a second, explainable score per wallet, shown ALONGSIDE
(never replacing) `combined_score`:
```
confidence_score = BASE_WEIGHT * combined_score
                  + ENTITY_WEIGHT        (its address entity also contains another flagged/lead wallet)
                  + CORRELATION_WEIGHT   (its address entity has a recorded correlation finding)
                  + PATTERN_WEIGHT       (it appears in a peeling chain or a CoinJoin-like transaction)
```
`BASE_WEIGHT=0.55, ENTITY_WEIGHT=0.15, CORRELATION_WEIGHT=0.10, PATTERN_WEIGHT=0.20` (sum to 1.0, so the score
stays in `[0, 1]`). Plain module constants, not config-overridable -- deliberately the same style as
`fusion.py`'s `HIGH_SHARE`/`MEDIUM_SHARE`, as instructed. Each of the three boost signals is all-or-nothing so
every point of the score traces back to one named, human-readable reason, stored as its own row (same
explainability spirit as the forensic rules). Deliberately NOT folded in: on-demand risk propagation, which stays
a separate, unstored, per-query investigator tool (Phase 3), not a per-wallet answer.
Reuses `entity_queries.linked_wallets_for_entities` (renamed from `_entities_by_wallet`'s sibling,
`entity_ids_for_wallet`, made public alongside it) rather than re-deriving wallet<->entity membership a third
time. Unlike `entities.py`/`correlation.py`/`coinjoin.py`, this module is NOT part of the blind, address-only
entity-building step, so it reads `sender_wallet`/`receiver_wallet` freely (needed to check pattern involvement)
and is not subject to their leak-field restriction.

**Storage (2 new additive tables, table count 28 -> 30):** `confidence_scores` (one row per scored wallet: score,
`computed_at`), `confidence_signals` (one row per contributing signal: `signal_name`, `contribution`, plain-language
`detail`) -- so an investigator can see exactly which signals fired and by how much, the same way `forensic_findings`
lets them see exactly which rules triggered.

**Run control:** `python -m backend.cli compute-confidence [--source]`. Idempotent (replaces what was stored for
that source). Recommended order: after `build-entities` and `detect-patterns`, so it can see their signals; run
against no completed analysis and it reports `analysis_available: false` instead of erroring.

**Typology tags** (`backend/services/typology.py`) -- read-only, live-synthesized (no new table) per-wallet tags
over data that already exists: `Peeling chain`, `Possible CoinJoin`, `Correlated entity`, each with a
plain-language reason. A wallet can carry more than one. Exposed as its own endpoint rather than an addition to
the existing wallet-analysis response, so nothing about that response's shape changes.

**Geo aggregation** (`backend/services/geo_queries.py`) -- read-only, built on `flow_records.geo_country/asn/asn_org`
(NOT `tx_details`, where an earlier draft of this instruction placed them; corrected here to the columns that
actually carry them, `analysis/correlation.py`'s docstring names the same table). One wallet's traffic
(`GET /api/wallets/{id}/geo`) and a dataset-wide summary across every current investigative lead
(`GET /api/geo/summary`, for a Dashboard panel). Always labelled synthetic GeoIP demo data, same caution as
everywhere else this data appears.

**API (5 new endpoints, count 56 -> 61 measured via the OpenAPI schema's path count):**
`GET /api/confidence-scores` (paging; filter `min_score`), `GET /api/wallets/{id}/confidence`,
`GET /api/wallets/{id}/typology`, `GET /api/wallets/{id}/geo`, `GET /api/geo/summary`. All in a new
`backend/routers/insights.py` rather than edited into `wallets.py`/`patterns.py`, per this phase's own preference
for new files over edited ones. (Measuring the same way at the `sih146-checkpoint-phase3-done` tag gives 56 paths,
not the 61 that phase's own report cited -- that earlier figure evidently counted something else; 56 -> 61 here
is measured fresh and consistently, both ends via the live OpenAPI schema.)

## Frontend for wallet insights (Phase 4, Part B)
Same design-system reuse discipline as Phase 3 Part B: no new CSS file, no new component, every new piece built
from `Panel`, `data-table`, `rule-tag`/`rule-tag-inline`, and the `reasons`/`evidence-col` list styles that already
existed for the forensic-rules panel.

- **Confidence column** on `WalletTable.tsx` (shared by the Dashboard priority queue and the Anomalies table): an
  optional `confidenceScores` prop (`{wallet -> score}`); the column itself is only rendered when the prop is
  passed, so CSV-fallback mode (where the feature does not exist) shows the table exactly as before. Fetched once
  via a new shared hook, `useConfidenceScores()` (`state/data.tsx`), from `GET /api/confidence-scores?limit=500` --
  the one hook lives in the shared hooks file (unlike Phase 3's per-page `useCoinjoinIds`) because two independent
  top-level pages (Dashboard, Anomalies) need the identical map, not one page's two internal views.
- **Confidence sub-section** inside WalletDetail's existing "Why was this wallet flagged?" panel: a new
  `evidence-col` block *after* the existing ML/forensic grid (not one of its two grid cells, so the 2-column grid
  itself is untouched), listing every stored signal with its contribution and plain-language reason, `reasons`-list
  styled the same as the panel's own rule list. Fetches `GET /api/wallets/{id}/confidence`; a 404 (score not yet
  computed for this wallet) renders nothing, not an error -- the ML/forensic/combined-result display above it is
  completely unaffected either way.
- **Typology badges**: `rule-tag rule-tag-inline` spans next to the wallet id, same mechanism as the existing
  "repeated"/"possible CoinJoin" badges. On WalletDetail's title (one `GET /api/wallets/{id}/typology` call). On
  the Anomalies table, typology has no bulk list endpoint (by Part A's own design -- a per-wallet, on-demand
  synthesis, not a stored table), so tags are fetched only for the wallets on the CURRENT page (`Promise.all` over
  at most 25 requests, refetched when the visible page's wallet ids change) rather than once for the whole dataset.
- **Geographic footprint panel** on WalletDetail, next to `RelatedEntities`: a `data-table` of country/ASN counts
  from `GET /api/wallets/{id}/geo`, always captioned `synthetic`.
- **"Top countries in leads"** panel on the Dashboard, below the existing two-panel grid (not inside it, since that
  grid is a fixed 2-column layout and a 3rd item would wrap awkwardly): from `GET /api/geo/summary`.

No backend file was touched in Part B; every one of the six changed files is under `frontend/src/`.

## Credits
IP geolocation by DB-IP.com (https://db-ip.com), CC BY 4.0. Country and ASN lookups use the DB-IP Lite databases in `data/geoip/`.
