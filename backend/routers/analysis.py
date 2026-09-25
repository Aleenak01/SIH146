from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.ml_bridge import AnalysisError
from ..analysis.service import AnalysisBusy, analysis_is_stale, latest_run, run_analysis
from ..database import Database
from ..deps import get_db, get_session
from ..errors import AppError
from ..models import AnalysisRun
from ..schemas import AnalysisRunOut, BulkAnalysis, RunSummaryOut
from ..services import leads as leads_service

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _run_out(r: AnalysisRun) -> AnalysisRunOut:
    return AnalysisRunOut(run_id=r.run_id, source=r.source, trigger=r.trigger, status=r.status, started_at=r.started_at, finished_at=r.finished_at,
                          transfer_count=r.transfer_count, wallet_count=r.wallet_count, error=r.error, run_config=r.model_config_json)


@router.post("/run", response_model=RunSummaryOut)
def run_now(db: Database = Depends(get_db), source: Literal["synthetic", "real_bitcoin"] = "synthetic"):
    """
    Run the analysis now (features -> Isolation Forest -> forensic rules -> fusion -> leads) over the transactions of one
    source (default synthetic). The monitor does this automatically for synthetic data; this is the manual equivalent.
    Real data is analysed only when asked for here, on its own, never mixed with the synthetic data. Wallets with fewer
    than two transactions cannot be scored, and at least 20 scoreable wallets are required.
    """
    try:
        return RunSummaryOut(**run_analysis(db, source, trigger="manual").as_dict())
    except AnalysisBusy as e:
        raise AppError(409, "analysis_in_progress", str(e)) from e
    except AnalysisError as e:
        raise AppError(422, "analysis_unavailable", str(e)) from e


@router.get("/runs", response_model=list[AnalysisRunOut])
def runs(session: Session = Depends(get_session), limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return [_run_out(r) for r in session.scalars(select(AnalysisRun).order_by(AnalysisRun.run_id.desc()).limit(limit))]


@router.get("/latest")
def latest(session: Session = Depends(get_session)):
    run = latest_run(session, "synthetic")
    if run is None:
        raise AppError(404, "not_found", "No analysis has completed yet.")
    return {**_run_out(run).model_dump(mode="json"), "stale": analysis_is_stale(session, "synthetic")}


@router.get("/wallets", response_model=BulkAnalysis)
def all_wallets(session: Session = Depends(get_session)):
    """
    The latest analysis of every scored wallet in one response (ML score and prediction, forensic rules and
    explanations, combined result, priority, lead state, cases, review status and the 18 features). Wallets that
    could not be scored are not invented; they are counted in `unscored_wallets`.
    """
    return leads_service.bulk_analysis(session, "synthetic")
