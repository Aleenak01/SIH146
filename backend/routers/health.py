from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..deps import get_session
from ..analysis.service import analysis_is_stale, latest_run
from ..models import AnomalyResult, Case, EntityCluster, InvestigativeLead, NetworkObservation, Transaction, Wallet
from ..schemas import OverviewOut

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(session: Session = Depends(get_session)):
    """Liveness check that also proves the database answers. Exposes no secrets or paths."""
    session.scalar(select(func.count()).select_from(Wallet))
    return {"status": "ok", "version": __version__, "database": "sqlite", "mode": "local"}


@router.get("/overview", response_model=OverviewOut)
def overview(session: Session = Depends(get_session)):
    """Record counts, split by data source (synthetic / real_bitcoin)."""
    by_source: dict[str, dict[str, int]] = {}
    for source, n in session.execute(select(Transaction.source, func.count()).group_by(Transaction.source)):
        by_source.setdefault(source, {"transactions": 0, "wallets": 0})["transactions"] = n
    for source, n in session.execute(select(Wallet.source, func.count()).group_by(Wallet.source)):
        by_source.setdefault(source, {"transactions": 0, "wallets": 0})["wallets"] = n
    first, last = session.execute(select(func.min(Transaction.timestamp), func.max(Transaction.timestamp))).one()
    run = latest_run(session, "synthetic")
    anomalous = leads = None
    if run is not None:
        anomalous = session.scalar(select(func.count()).select_from(AnomalyResult).where(AnomalyResult.run_id == run.run_id, AnomalyResult.anomaly_prediction == "Anomalous"))
        leads = session.scalar(select(func.count()).select_from(InvestigativeLead).where(InvestigativeLead.source == "synthetic"))
    return OverviewOut(
        last_analysis_at=run.finished_at if run else None, analysis_stale=analysis_is_stale(session, "synthetic"),
        anomalous_wallets=anomalous, leads_total=leads,
        network_observations_total=session.scalar(select(func.count()).select_from(NetworkObservation)) or 0,
        clusters_total=session.scalar(select(func.count()).select_from(EntityCluster)) or 0,
        cases_total=session.scalar(select(func.count()).select_from(Case)) or 0,
        active_cases=session.scalar(select(func.count()).select_from(Case).where(Case.status != "Closed")) or 0,
        transactions_total=sum(v["transactions"] for v in by_source.values()),
        wallets_total=sum(v["wallets"] for v in by_source.values()),
        by_source=by_source, first_transaction_at=first, last_transaction_at=last,
    )
