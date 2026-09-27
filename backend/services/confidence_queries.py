"""Queries behind the confidence-score endpoints (Phase 4 Part A). See analysis/confidence.py for how the score is computed."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.confidence import CONFIDENCE_LABEL
from ..models import ConfidenceScore, ConfidenceSignal


def _signals_out(session: Session, wallet_addresses: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not wallet_addresses:
        return {}
    rows = session.scalars(select(ConfidenceSignal).where(ConfidenceSignal.wallet_address.in_(wallet_addresses)).order_by(ConfidenceSignal.signal_id))
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r.wallet_address, []).append({"signal_name": r.signal_name, "contribution": r.contribution, "detail": r.detail})
    return out


def list_confidence_scores(session: Session, *, source: str | None, min_score: float | None, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    stmt = select(ConfidenceScore)
    if source:
        stmt = stmt.where(ConfidenceScore.source == source)
    rows = list(session.scalars(stmt))
    if min_score is not None:
        rows = [r for r in rows if r.score >= min_score]
    rows.sort(key=lambda r: (-r.score, r.wallet_address))
    total = len(rows)
    page = rows[offset: offset + limit]
    signals = _signals_out(session, [r.wallet_address for r in page])
    return total, [{"wallet_address": r.wallet_address, "source": r.source, "score": r.score, "computed_at": r.computed_at,
                    "signals": signals.get(r.wallet_address, [])} for r in page]


def get_wallet_confidence(session: Session, wallet: str) -> dict[str, Any] | None:
    row = session.get(ConfidenceScore, wallet)
    if row is None:
        return None
    signals = _signals_out(session, [wallet])
    return {"wallet_address": row.wallet_address, "source": row.source, "score": row.score, "computed_at": row.computed_at,
            "signals": signals.get(wallet, []), "label": CONFIDENCE_LABEL}
