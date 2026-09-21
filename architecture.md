# Architecture

**Project:** SIH146 — AI Powered Monitoring and Analysis of Bitcoin Transactions

Keep this file in sync with the code. Sections are labelled **Implemented**, **Planned** or
**Future/optional**. Nothing marked Planned or Future exists yet.

## Purpose and principle
An offline system that analyses synthetic Bitcoin transaction and network metadata, identifies
anomalous patterns, prioritises investigative leads and presents explainable findings to an
investigator. An anomaly is an investigative signal for review, **not** proof of criminal or
fraudulent activity.

## Implementation status
| Component | Status |
|---|---|
| Python virtual environment, pandas/numpy | **Implemented** |
| Synthetic raw dataset generator (`generate_dataset.py`) and CSV | **Implemented** |
| Feature engineering (`ml/feature_engineering.py`) and `data/wallet_behavior_features.csv` | **Implemented** |
| Isolation Forest (`ml/anomaly_detection.py`) and `data/anomaly_results.csv` | **Implemented** |
| Forensic behavioural rules (`ml/forensic_rules.py`) and `data/forensic_results.csv` | **Implemented** |
| Project documentation | **Implemented** |
| Clustering | Planned |
| Investigation priority / risk scoring | Planned |
| Final investigation workflow | Planned |
| Backend / API | Planned |
| React investigator dashboard | Planned |
| Cytoscape.js network graph | Planned |
| IP/network entities in the data and graph | Future/optional |

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
- Wallets with fewer than 2 transactions would get NaN time features (none in the current data).

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

## Planned: system flow
Input: synthetic Bitcoin/network metadata.

```
Data Ingestion → Parsing & Normalization → Correlation → Relationship Building
  → Behavioural Feature Extraction → ML Analysis → Risk/Confidence
  → Lead Prioritization → Explainable Findings
```
Output: Investigation Dashboard.

## Planned: ML pipeline
```
Raw Bitcoin Transaction Data
  → Data Preprocessing
  → Feature Extraction
  → Behavioural Features
  → Isolation Forest
  → Anomaly Score
  → Forensic Rule Engine
  → Evidence + Explanation
  → Investigator UI
```
Built so far: raw data, feature extraction, behavioural features, Isolation Forest, anomaly score, forensic behavioural rules and evidence/explanation. The investigator UI, priority scoring and everything after are not built.

**Model:** Isolation Forest, an unsupervised anomaly-detection algorithm, used for the initial
prototype. It receives engineered behavioural features, not only raw transactions.

**Planned feature groups** (examples, not final):
1. Transaction behaviour: transaction amount, input/output count
2. Wallet activity: transaction frequency, transaction count, incoming/outgoing volume
3. Time behaviour: time between transactions, dormancy duration, activity bursts
4. Fund flow: fan-in/fan-out, fund splitting/distribution and concentration
5. Network/relationship behaviour: wallet degree, unique/repeated connections, transaction paths,
   hop distance

**Patterns the prototype should be designed to surface:** rapid movement of funds, dormant wallet
activation, high-frequency activity, fund splitting/distribution, fund concentration,
unusual/repeated wallet relationships, unusual transaction behaviour. These are investigative
signals only.

## Planned: network graph
Generated from relationships identified during processing; rendered on the frontend with
Cytoscape.js.
- Possible nodes: wallets, transactions, IP/network entities where available.
- Possible edges: wallet → wallet, wallet → transaction, transaction → wallet, wallet → IP/network
  entity.
- Purpose: show connected wallets, movement paths, clusters, central entities, transaction chains,
  and relationships between otherwise separate records.

The current dataset supports wallet → wallet relationships only (`wallet_address` and
`counterparty_wallet`). Transaction nodes and IP/network entities would need additional data and are
Future/optional.

## Planned: dashboard structure
Planned UI structure, not necessarily fully implemented:
```
PS146
├── Overview
├── Investigative Leads
├── Investigation
│   ├── Summary
│   ├── Findings
│   ├── Evidence
│   ├── Transactions
│   ├── Timeline
│   └── Network Graph
└── Data Ingestion
```
Planned dashboard content: prioritised investigative leads, risk/confidence information, explanation
of why something was flagged, related wallets/transactions/network information, relationship graph,
timeline, supporting evidence, investigation summary.
