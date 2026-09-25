from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..errors import AppError
from ..schemas import LeadDetail, LeadPage
from ..services import leads as leads_service

router = APIRouter(prefix="/api/leads", tags=["investigative leads"])


@router.get("", response_model=LeadPage)
def list_leads(
    session: Session = Depends(get_session),
    q: Annotated[str | None, Query(max_length=128)] = None,
    priority_level: Literal["High", "Medium", "Low"] | None = None,
    ml_prediction: Literal["Anomalous", "Normal"] | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    source: Literal["synthetic", "real_bitcoin"] | None = None,
    sort: Literal["priority_rank", "combined_score", "ml_score", "first_flagged_at", "wallet_address"] = "priority_rank",
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """
    Investigative leads ranked by combined score (prototype weighting, not validated). A lead is a wallet the
    Isolation Forest flags OR that ranks in the High priority band (top 5% by combined score). A lead is NOT a case.
    """
    total, items = leads_service.list_leads(session, q=q, priority_level=priority_level, ml_prediction=ml_prediction, min_score=min_score,
                                            source=source, sort=sort, order=order, limit=limit, offset=offset)
    return LeadPage(total=total, limit=limit, offset=offset, items=items)


@router.get("/{wallet_address}", response_model=LeadDetail)
def get_lead(wallet_address: str, session: Session = Depends(get_session)):
    """One lead with its full evidence: ML, forensic findings, features, fusion, related transactions and wallets."""
    lead = leads_service.get_lead_detail(session, wallet_address)
    if lead is None:
        raise AppError(404, "not_found", f"No lead for wallet {wallet_address!r}. (Every wallet has an analysis at /api/wallets/{{address}}/analysis.)")
    return lead
