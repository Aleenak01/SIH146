"""
Runs the analysis pipeline over the transactions in the database and stores the results.

    transactions (SQLite) -> ml/ pipeline (unchanged) -> wallet_features, anomaly_results, forensic_findings
                          -> fusion, priority -> investigative_leads

Each run re-analyses ONE source's whole population (Isolation Forest and the percentile rules are population-
relative, so a single new transaction can shift other wallets' scores). Detail rows of older runs are pruned, so
the tables always hold the latest run; `analysis_runs` keeps a short history.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select

from ..database import Database
from ..models import (
    FEATURE_COLUMNS, AnalysisRun, AnomalyResult, ForensicFinding, FusionResult, InvestigativeLead, Transaction, WalletFeatures, utcnow,
)
from ..services.ingest import WRITE_LOCK
from . import fusion as fusion_service
from .ml_bridge import AnalysisError, run_pipeline
from .rules_meta import rule_label

ANALYSIS_LOCK = threading.Lock()        # one analysis at a time
RUN_HISTORY = 50                        # analysis_runs rows kept per source


class AnalysisBusy(RuntimeError):
    """An analysis is already running."""


@dataclass
class RunSummary:
    run_id: int
    status: str
    source: str
    trigger: str
    transfer_count: int
    wallet_count: int
    scored_wallets: int
    unscored_wallets: int
    anomalous_wallets: int
    leads_total: int
    new_leads: int
    duration_seconds: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def latest_run(session, source: str = "synthetic") -> AnalysisRun | None:
    return session.scalars(
        select(AnalysisRun).where(AnalysisRun.source == source, AnalysisRun.status == "completed").order_by(AnalysisRun.run_id.desc()).limit(1)
    ).first()


def analysis_is_stale(session, source: str = "synthetic") -> bool:
    """True if there are transactions the latest completed analysis has not seen."""
    n = session.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == source)) or 0
    if n == 0:
        return False
    run = latest_run(session, source)
    return run is None or run.transfer_count != n


def run_analysis(db: Database, source: str = "synthetic", trigger: str = "manual") -> RunSummary:
    if not ANALYSIS_LOCK.acquire(blocking=False):
        raise AnalysisBusy("An analysis is already running.")
    started = time.perf_counter()
    try:
        with db.transaction() as s:
            run = AnalysisRun(source=source, trigger=trigger, status="running")
            s.add(run)
            s.flush()
            run_id = run.run_id

        try:
            with db.session() as s:
                transfers = [
                    tuple(r) for r in s.execute(
                        select(Transaction.timestamp, Transaction.sender_wallet, Transaction.receiver_wallet, Transaction.amount_btc,
                               Transaction.input_count, Transaction.output_count)
                        .where(Transaction.source == source).order_by(Transaction.timestamp, Transaction.transaction_id)
                    )
                ]
            out = run_pipeline(transfers)                       # the heavy part; no database locks are held
            with WRITE_LOCK, db.transaction() as s:
                summary = _persist(s, run_id, source, trigger, transfers, out, started)
            return summary
        except Exception as e:
            with db.transaction() as s:
                failed = s.get(AnalysisRun, run_id)
                failed.status, failed.finished_at, failed.error = "failed", utcnow(), f"{type(e).__name__}: {e}"[:1000]
            raise
    finally:
        ANALYSIS_LOCK.release()


def _persist(s, run_id: int, source: str, trigger: str, transfers, out, started: float) -> RunSummary:
    now = utcnow()
    features = out.features
    ml = out.ml.set_index("wallet_address")
    fusion_rows = out.fusion.set_index("wallet_address")
    ranks = fusion_service.rank_wallets(out.fusion)
    n = len(ranks)
    total_rules = out.config["fusion"]["total_forensic_rules"]

    # ---- per-wallet results of this run ----------------------------------------------------------
    feature_rows = [
        {"run_id": run_id, "wallet_address": rec["wallet_address"], **{c: float(rec[c]) for c in FEATURE_COLUMNS}}
        for rec in features.to_dict("records")
    ]
    anomaly_rows = [
        {"run_id": run_id, "wallet_address": w, "anomaly_score": float(r["anomaly_score"]), "anomaly_prediction": r["anomaly_prediction"]}
        for w, r in ml.iterrows()
    ]
    fusion_result_rows = [
        {"run_id": run_id, "wallet_address": w, "forensic_score": float(r["forensic_score"]), "forensic_rule_count": int(r["forensic_rule_count"]),
         "evidence_level": r["forensic_evidence_level"], "combined_score": float(r["combined_score"]),
         "priority_rank": ranks[w][0], "priority_level": ranks[w][1]}
        for w, r in fusion_rows.iterrows()
    ]
    finding_rows = [
        {"run_id": run_id, "wallet_address": w, "rule_id": f["rule_id"], "rule_name": rule_label(f["rule_id"]), "severity": None,
         "feature": f["feature"], "feature_value": f["value"], "threshold": f["threshold"], "evidence": f["evidence"], "created_at": now}
        for w, items in out.findings.items() for f in items
    ]
    for model, rows in ((WalletFeatures, feature_rows), (AnomalyResult, anomaly_rows), (FusionResult, fusion_result_rows), (ForensicFinding, finding_rows)):
        if rows:
            s.execute(insert(model), rows)

    # ---- prune the previous runs' detail rows (this source only) ---------------------------------------
    old_runs = select(AnalysisRun.run_id).where(AnalysisRun.source == source, AnalysisRun.run_id != run_id)
    for model in (WalletFeatures, AnomalyResult, FusionResult, ForensicFinding):
        s.execute(delete(model).where(model.run_id.in_(old_runs)))

    # ---- investigative leads: upsert, keep first_flagged_at, drop wallets that stopped qualifying -------
    existing = {l.wallet_address: l for l in s.scalars(select(InvestigativeLead).where(InvestigativeLead.source == source))}
    wanted: set[str] = set()
    new_leads = 0
    for w, row in fusion_rows.iterrows():
        rank, level = ranks[w]
        if not fusion_service.is_lead(row["ml_anomaly_prediction"], level):
            continue
        wanted.add(w)
        findings = out.findings.get(w, [])
        reasons = fusion_service.build_reasons(
            ml_score=float(row["ml_anomaly_score"]), ml_prediction=row["ml_anomaly_prediction"], rule_ids=[f["rule_id"] for f in findings],
            total_rules=total_rules, combined=float(row["combined_score"]), rank=rank, n=n, level=level,
            ml_weight=float(row["ml_weight"]), forensic_weight=float(row["forensic_weight"]))
        evidence = fusion_service.contributing_evidence(row=row, findings=findings, rank=rank, n=n, level=level, reasons=reasons)
        lead = existing.get(w)
        if lead is None:
            lead = InvestigativeLead(wallet_address=w, source=source, first_flagged_at=now)
            s.add(lead)
            new_leads += 1
        lead.run_id = run_id
        lead.ml_score, lead.ml_prediction = float(row["ml_anomaly_score"]), row["ml_anomaly_prediction"]
        lead.forensic_score, lead.forensic_rule_count = float(row["forensic_score"]), int(row["forensic_rule_count"])
        lead.evidence_level = row["forensic_evidence_level"]
        lead.combined_score, lead.priority_rank, lead.priority_level = float(row["combined_score"]), rank, level
        lead.contributing_evidence = evidence
    for w, lead in existing.items():
        if w not in wanted:
            s.delete(lead)

    # ---- run record -------------------------------------------------------------------------------------
    run = s.get(AnalysisRun, run_id)
    run.status, run.finished_at = "completed", now
    run.transfer_count = len(transfers)
    run.wallet_count = n + len(out.unscored)
    run.model_config_json = {**out.config, "unscored_examples": dict(list(out.unscored.items())[:50]),
                             "priority_bands": fusion_service.PRIORITY_BAND_LABEL}
    old_ids = s.scalars(select(AnalysisRun.run_id).where(AnalysisRun.source == source).order_by(AnalysisRun.run_id.desc()).offset(RUN_HISTORY)).all()
    if old_ids:
        s.execute(delete(AnalysisRun).where(AnalysisRun.run_id.in_(old_ids)))

    anomalous = int((ml["anomaly_prediction"] == "Anomalous").sum())
    return RunSummary(
        run_id=run_id, status="completed", source=source, trigger=trigger, transfer_count=len(transfers), wallet_count=n + len(out.unscored),
        scored_wallets=n, unscored_wallets=len(out.unscored), anomalous_wallets=anomalous, leads_total=len(wanted), new_leads=new_leads,
        duration_seconds=round(time.perf_counter() - started, 2))
