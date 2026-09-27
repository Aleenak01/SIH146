"""
Wallet insights (Phase 4 Part A): the confidence score, typology tags and synthetic-GeoIP geography built on top of
the existing wallet-level and Phase 2/3 data. All read-only, all additive -- no existing endpoint's shape changes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..errors import AppError
from ..services import confidence_queries, geo_queries, typology

router = APIRouter(tags=["wallet insights (synthetic, phase 4)"])


@router.get("/api/confidence-scores")
def list_confidence_scores(
    session: Session = Depends(get_session),
    source: Literal["synthetic", "real_bitcoin"] | None = "synthetic",
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Every wallet's confidence score (highest first), each with its contributing signals. See `compute-confidence`."""
    total, items = confidence_queries.list_confidence_scores(session, source=source, min_score=min_score, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/api/wallets/{wallet}/confidence")
def get_wallet_confidence(wallet: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """One wallet's confidence score and the individual signals that contributed to it."""
    data = confidence_queries.get_wallet_confidence(session, wallet)
    if data is None:
        raise AppError(404, "not_found", f"No confidence score for wallet {wallet!r}. Run `compute-confidence` first.")
    return data


@router.get("/api/wallets/{wallet}/typology")
def get_wallet_typology(wallet: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Small, human-readable tags (Peeling chain / Possible CoinJoin / Correlated entity) synthesized live from Phase 2/3 data."""
    data = typology.wallet_typology(session, wallet)
    if data is None:
        raise AppError(404, "not_found", f"No wallet {wallet!r}.")
    return data


@router.get("/api/wallets/{wallet}/geo")
def get_wallet_geo(wallet: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Country/ASN breakdown of this wallet's transaction traffic (synthetic GeoIP)."""
    data = geo_queries.wallet_geo(session, wallet)
    if data is None:
        raise AppError(404, "not_found", f"No wallet {wallet!r}.")
    return data


@router.get("/api/geo/summary")
def get_geo_summary(
    session: Session = Depends(get_session),
    source: Literal["synthetic", "real_bitcoin"] = "synthetic",
    top: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Dataset-wide top countries/ASNs across every current investigative lead (synthetic GeoIP; for a Dashboard panel)."""
    return geo_queries.geo_summary(session, source=source, top=top)
