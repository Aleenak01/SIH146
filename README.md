# SIH146 — AI Powered Monitoring and Analysis of Bitcoin Transactions

## Problem
Continuously monitoring cryptocurrency transactions and manually spotting suspicious patterns is
slow and error-prone. Bitcoin data is highly interconnected, and suspicious activity may not be
visible in a single transaction: it can require examining multiple transactions, wallets, network
information and timestamps together (unusual behaviour, fund movement across wallets, links between
otherwise unrelated wallets).

## Solution (planned)
An offline AI/ML-powered Bitcoin transaction monitoring and forensic investigation system. It
analyses synthetic transaction and network metadata, identifies anomalous patterns, prioritises
investigative leads, and presents explainable findings to an investigator. The system does **not**
claim that an anomaly proves criminal or fraudulent activity; it flags unusual or potentially
suspicious behaviour for investigator review. The initial ML approach is Isolation Forest.

## Current implementation status
| Implemented | Not implemented yet |
|---|---|
| Python virtual environment (`.venv`) | Investigation priority / risk scoring |
| pandas / numpy / scikit-learn environment | Clustering |
| Synthetic raw Bitcoin transaction dataset + generator | Backend / API |
| Feature engineering pipeline (`ml/feature_engineering.py`) | React frontend / dashboard |
| Wallet behaviour feature dataset (410 wallets, 18 features) | Cytoscape.js network graph |
| Isolation Forest anomaly detection (`ml/anomaly_detection.py`) | Final investigation workflow |
| Anomaly results dataset (`data/anomaly_results.csv`) | |
| Forensic behavioural rules (`ml/forensic_rules.py`) | |
| Forensic results dataset (`data/forensic_results.csv`) | |
| Project documentation files | |

The project has completed raw data generation, feature engineering, Isolation Forest anomaly
detection and forensic behavioural rules. "Anomalous" means statistically unusual behaviour that needs investigator review, not
confirmed suspicious or criminal activity.

## Project structure
```
.
├── .venv/                       # virtual environment (pandas, numpy, scikit-learn)
├── .vscode/settings.json        # points VS Code at .venv
├── dataset/
│   └── synthetic_bitcoin_transactions.csv   # generated raw data (10,000 rows, 7 columns)
├── data/
│   ├── wallet_behavior_features.csv         # engineered wallet-level features (410 rows)
│   ├── anomaly_results.csv                  # Isolation Forest output (410 rows)
│   └── forensic_results.csv                 # rule-based behavioural evidence (410 rows)
├── ml/
│   ├── feature_engineering.py   # raw transactions -> wallet behaviour features
│   ├── anomaly_detection.py     # Isolation Forest on the wallet features
│   └── forensic_rules.py        # behavioural rules + explanations
├── generate_dataset.py          # generates and validates the raw dataset
├── README.md
├── architecture.md              # detailed technical architecture
└── session.md                   # development log
```

## Run the implemented components
**1. Dataset generator**
From the project root (PowerShell):
```powershell
.\.venv\Scripts\python.exe generate_dataset.py
```
Regenerates `dataset/synthetic_bitcoin_transactions.csv` (fixed seed 42, reproducible) and prints a
validation summary. If `.venv` is missing: `python -m venv .venv`, then
`.\.venv\Scripts\python.exe -m pip install pandas numpy scikit-learn`.

**2. Feature engineering** (reads the raw CSV, writes `data/wallet_behavior_features.csv`, prints a validation summary)
```powershell
.\.venv\Scripts\python.exe ml\feature_engineering.py
```

**3. Anomaly detection** (reads `data/wallet_behavior_features.csv`, writes `data/anomaly_results.csv`)
```powershell
.\.venv\Scripts\python.exe ml\anomaly_detection.py
```
Output columns: `wallet_address`, `anomaly_score` (higher = more unusual), `anomaly_prediction`
(`Anomalous` / `Normal`).

**4. Forensic behavioural rules** (reads the features and anomaly results, writes `data/forensic_results.csv`)
```powershell
.\.venv\Scripts\python.exe ml\forensic_rules.py
```
Output columns: `wallet_address`, `anomaly_score`, `anomaly_prediction` (copied unchanged),
`rule_count`, `evidence_level` (describes how many indicators triggered; not a risk score),
`triggered_rules`, `explanation`. Triggered rules are behavioural indicators of unusual behaviour,
not proof of criminal activity.

Raw dataset time window: 2025-10-01 to 2026-09-24 (about one year, roughly balanced across months;
synthetic, not real blockchain data).  
Raw dataset columns (fixed): `timestamp`, `wallet_address`, `amount_btc`, `direction`, `input_count`,
`output_count`, `counterparty_wallet`.

## Frontend
Offline investigator UI in `frontend/` (Vite + React + TypeScript, Recharts, React Flow). It reads the
existing CSV outputs directly; no backend or network access is needed.
```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # type-check + production build
```
Screens: Dashboard, Anomalies, Wallet investigation, Cases and Case detail, Transactions / Network,
Settings. Cases are created only by an investigator and stored in the browser's localStorage for the
prototype. Exports (CSV/JSON) are generated locally in the browser.

## More information
- Technical architecture, ML pipeline, planned UI: [architecture.md](architecture.md)
- Development history: [session.md](session.md)
