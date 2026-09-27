"""Pattern-detector endpoints (Phase 3 Part A): peeling chains, CoinJoin-like candidates, and on-demand risk propagation. Heuristic signals, never proof."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..analysis.coinjoin import NOTE as COINJOIN_NOTE
from ..analysis.risk_propagation import NOTE as RISK_NOTE, propagate_risk
from ..config import Settings
from ..deps import get_session, get_settings
from ..errors import AppError
from ..services import pattern_queries

router = APIRouter(tags=["pattern detectors (synthetic, phase 3)"])


@router.get("/api/peeling-chains")
def list_peeling_chains(
    session: Session = Depends(get_session),
    source: Literal["synthetic", "real_bitcoin"] | None = "synthetic",
    wallet: Annotated[str | None, Query(max_length=128, description="Only chains that pass through this wallet")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """
    Peeling chains: sequences of wallet-level transfers where each hop forwards most of what it just received. A
    heuristic pattern (large, repeated "change"); an investigative signal, never proof of layering or laundering.
    """
    total, items = pattern_queries.list_peeling_chains(session, source=source, wallet=wallet, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/api/peeling-chains/{chain_id}")
def get_peeling_chain(chain_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """One peeling chain with all of its hops, in order."""
    detail = pattern_queries.get_peeling_chain_detail(session, chain_id)
    if detail is None:
        raise AppError(404, "not_found", f"No peeling chain {chain_id!r}.")
    return detail


@router.get("/api/coinjoin-candidates")
def list_coinjoin_candidates(
    session: Session = Depends(get_session),
    source: Literal["synthetic", "real_bitcoin"] | None = "synthetic",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """CoinJoin-like transaction candidates: several distinct input addresses with several near-equal outputs. A heuristic flag, never a certainty."""
    total, items = pattern_queries.list_coinjoin_candidates(session, source=source, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": items, "note": COINJOIN_NOTE}


class RiskPropagateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_wallets: Annotated[list[str], Field(min_length=1, max_length=50)]
    max_hops: Annotated[int | None, Field(default=None, ge=1, le=10)] = None
    decay_per_hop: Annotated[float | None, Field(default=None, gt=0, le=1)] = None


@router.post("/api/risk/propagate")
def risk_propagate(body: RiskPropagateIn, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """
    On-demand risk propagation from one or more seed wallets (initial score 1.0), decayed per hop along the wallet
    transfer graph. Nothing is stored; call again any time with different seeds. A heuristic investigator aid, not a
    validated risk score.
    """
    decay = body.decay_per_hop if body.decay_per_hop is not None else settings.risk_decay_per_hop
    max_hops = body.max_hops if body.max_hops is not None else settings.risk_max_hops
    results = propagate_risk(session, body.seed_wallets, decay_per_hop=decay, max_hops=max_hops, max_nodes=settings.risk_max_nodes)
    return {
        "seed_wallets": list(dict.fromkeys(body.seed_wallets)), "decay_per_hop": decay, "max_hops": max_hops, "note": RISK_NOTE,
        "items": [{"wallet": r.wallet, "propagated_score": round(r.propagated_score, 6), "hop_distance": r.hop_distance, "path": list(r.path)} for r in results],
    }
