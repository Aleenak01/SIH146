# SIH146 — Technical Report

*A short technical write-up of the approach. For full detail see [architecture.md](architecture.md) (design
decisions, data model, API) and [README.md](README.md) (setup, features, demo walkthrough). For the phase-by-phase
build log see [session.md](session.md).*

## 1. Problem

Monitoring Bitcoin transactions for suspicious activity by hand does not scale: the data is highly interconnected,
and a single transaction rarely tells the whole story. Spotting unusual behaviour often requires correlating many
transactions, wallets, and (where available) network context over time. The goal of this project is a working
prototype of an AI/ML-assisted platform that surfaces **ranked, explainable investigative leads** for a human
investigator to review — never an automated verdict.

## 2. Approach

The system is built in additive layers, each one a separate, independently runnable step, so nothing later depends
on breaking something earlier:

```
Synthetic dataset ──▶ Feature engineering ──▶ Isolation Forest (ML)
                                          └──▶ Forensic behavioural rules
                                                       │
                                                  Result fusion ──▶ Investigative leads / priority ranking
                                                       │
                                     FastAPI + SQLite backend (cases, monitoring, search, graph)
                                                       │
                                          React + TypeScript investigator UI
```

Three further layers sit on top of the same data without altering it:
- **Address entities & network correlation** — a second, address-level view (common-input-ownership clustering,
  IP/ASN correlation).
- **Pattern detectors** — peeling-chain and CoinJoin-like detection, plus on-demand risk propagation.
- **Wallet insights** — an explainable confidence score, typology tags, and GeoIP geographic aggregation, closing
  the gap between a raw model score and a reviewable, reasoned lead.

### Key design principles
- **Synthetic data throughout.** Every transaction, wallet, IP address, device and session shown in the UI is
  generated or randomly assigned for the demo. An optional, off-by-default adapter can read a handful of real,
  public Bitcoin blocks (read-only) to prove the pipeline generalises, but the UI never displays it.
- **Explainable over black-box.** Every score a wallet receives — the ML anomaly score, the forensic rule count,
  the combined result, and (Phase 4) the confidence score — is broken into its named, human-readable contributing
  factors, the same way the forensic rules already explain themselves.
- **Heuristics are labelled as heuristics.** Address entities, correlation findings, peeling chains and CoinJoin
  candidates are investigative *signals*, stated in every response as "likely" or "a possible link," never as
  proof of wrongdoing.
- **The investigator decides.** No analysis step ever creates a case on its own. A flagged wallet or a lead is a
  starting point for review; a case exists only because a human investigator opened one.
- **Additive, not invasive.** Every later phase reads from, but never edits, the tables and logic built by earlier
  phases (proven by tests: the wallet-level pipeline's numbers are pinned and checked after every change).

## 3. Tech stack

| Layer | Technology |
|---|---|
| Dataset generation | Python, deterministic seeded synthetic transaction/network generators |
| Feature engineering + ML | pandas, numpy, scikit-learn (Isolation Forest, unsupervised) |
| Forensic rules + fusion | Plain Python, population-relative percentile rules, a weighted-average combined score |
| Backend | FastAPI, SQLAlchemy 2.x, SQLite (WAL mode), Uvicorn |
| Graph / clustering | NetworkX (community detection) |
| GeoIP | MaxMind DB reader (`maxminddb`) against the offline, CC-BY-4.0 DB-IP Lite databases |
| Frontend | React 19 + TypeScript, Vite, React Router, React Flow (relationship graphs), Recharts (charts) |
| Tests | pytest, httpx (backend); `tsc`/`vite build` (frontend type-check) |

Everything runs fully offline and locally; no service in the stack requires the internet (the optional real-Bitcoin
adapter is the only exception, and it is off by default).

## 4. What was built, phase by phase

| Phase | What it adds | Storage | Status |
|---|---|---|---|
| 0 | Synthetic dataset (10,000 rows / 5,000 transfers / 410 wallets), feature engineering, Isolation Forest, forensic rules, result fusion (`ml/`, unchanged since) | 6 CSVs | Done |
| — | FastAPI + SQLite backend, ingestion, monitoring, leads, clusters, relationship graph, case management, search, settings, an optional real-Bitcoin adapter | ~20 tables | Done |
| 1 | Rich address-level transaction model (inputs/outputs/fees/script type) + synthetic network flow (IP/ASN/country) + multi-format import (CSV/JSON/JSONL/XML) | +4 tables | Done, not used by the analysis |
| 2 | Common-input-ownership address entities + network correlation (entity↔IP, entity↔entity, correlation findings) | +5 tables | Done, UI: "Entities" tab |
| 3 | Peeling-chain detection, CoinJoin-like detection (with a Phase-2 exclusion fix), on-demand risk propagation | +3 tables | Done, UI: badges, related-blocks, Anomalies filter, Wallet Detail panel |
| 4 | Explainable confidence score (combined score + 3 named corroborating signals), typology tags, GeoIP aggregation | +2 tables | Done, UI: Dashboard, Anomalies, Wallet Detail |

Current totals: 30 database tables, 61 API endpoints, 525 automated tests (514 backend + 11 pipeline), all passing.

## 5. Results at a glance (on the bundled synthetic dataset)

| Metric | Value |
|---|---|
| Wallets | 410 |
| ML-flagged (Isolation Forest, "Anomalous") | 41 |
| Investigative leads (flagged OR High priority band) | 42 |
| Related-entity clusters | 33 shared-network-observation + 19 transaction-community |
| Common-input-ownership address entities | 626 |
| Peeling chains detected | 232 (764 hops total, longest 6) |
| CoinJoin-like candidates | 0 (this generator was not designed to produce them — reported honestly, not hidden) |

Prototype thresholds throughout (the 40/60 forensic/ML fusion weight, the priority bands, the confidence-score
weights) are chosen for explainability and documented reasoning, **not statistically validated** — every screen and
API response that uses them says so.

## 6. How to run it

```bash
# Linux / macOS
bash scripts/setup.sh
bash scripts/run_backend.sh    # terminal 1 — http://127.0.0.1:8000
bash scripts/run_frontend.sh   # terminal 2 — http://localhost:5173
```
```powershell
# Windows
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
cd frontend; npm install; cd ..
.\.venv\Scripts\python.exe -m backend        # terminal 1
cd frontend; npm run dev                      # terminal 2
```
The app then offers **Load synthetic demo data** on first open. Full setup, an automated end-to-end check, and a
5-minute guided walkthrough are in [README.md](README.md).

## 7. Limitations and future work

- Single-user, local-only prototype: no authentication, no multi-user roles, no production deployment hardening.
- Analysis is micro-batch (a full re-run per new batch of transactions), not per-transaction streaming.
- ML/fusion/priority/confidence weights are prototype settings chosen for explainability, not fit or validated
  against labelled outcomes (there are none — this is unsupervised, synthetic data).
- The real-Bitcoin adapter reads a small, bounded slice of the chain for demonstration; it is not a general
  ingestion pipeline and its results are never shown in the UI.
- No containerized deployment (Docker) yet; the two processes (backend, frontend) are run directly.

## 8. Repository map

- `ml/` — the original, unmodified feature/ML/rules/fusion pipeline (command-line, CSV in/out).
- `backend/` — FastAPI application: `analysis/` (bridges to `ml/` plus every later-phase detector),
  `services/` (query and business logic), `routers/` (API endpoints), `models.py` (SQLAlchemy schema), `tests/`.
- `frontend/` — React + TypeScript investigator UI (`src/pages`, `src/components`, `src/graph`, `src/state`).
- `dataset/`, `data/` — the synthetic datasets and the local SQLite database (git-ignored once created).
- `scripts/` — `e2e_check.py` (automated demo/verification), `validate_entities.py` (offline entity-purity
  validation against a ground-truth file, not read by the backend), and the Linux/macOS `setup`/`run_*` scripts.
