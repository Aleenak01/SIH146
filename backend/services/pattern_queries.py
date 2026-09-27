"""Queries behind the pattern-detector endpoints (Phase 3 Part A): peeling chains and CoinJoin-like candidates."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.peeling import NOTE as PEELING_NOTE
from ..models import CoinJoinCandidate, PeelingChain, PeelingChainHop


def _chain_out(c: PeelingChain) -> dict[str, Any]:
    return {
        "chain_id": c.chain_id, "source": c.source, "start_wallet": c.start_wallet, "end_wallet": c.end_wallet,
        "hop_count": c.hop_count, "total_btc_start": c.total_btc_start, "total_btc_end": c.total_btc_end, "created_at": c.created_at,
    }


def list_peeling_chains(session: Session, *, source: str | None, wallet: str | None, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    stmt = select(PeelingChain)
    if source:
        stmt = stmt.where(PeelingChain.source == source)
    chains = list(session.scalars(stmt))
    if wallet:
        chain_ids_with_wallet = set(session.scalars(select(PeelingChainHop.chain_id).where(
            (PeelingChainHop.from_wallet == wallet) | (PeelingChainHop.to_wallet == wallet))))
        chains = [c for c in chains if c.chain_id in chain_ids_with_wallet]
    chains.sort(key=lambda c: (-c.hop_count, c.chain_id))
    total = len(chains)
    return total, [_chain_out(c) for c in chains[offset: offset + limit]]


def get_peeling_chain_detail(session: Session, chain_id: str) -> dict[str, Any] | None:
    c = session.get(PeelingChain, chain_id)
    if c is None:
        return None
    hops = list(session.scalars(select(PeelingChainHop).where(PeelingChainHop.chain_id == chain_id).order_by(PeelingChainHop.hop_index)))
    return {
        **_chain_out(c),
        "hops": [{"hop_index": h.hop_index, "from_wallet": h.from_wallet, "to_wallet": h.to_wallet,
                  "transaction_id": h.transaction_id, "amount_btc": h.amount_btc} for h in hops],
        "note": PEELING_NOTE,
    }


def list_coinjoin_candidates(session: Session, *, source: str | None, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    stmt = select(CoinJoinCandidate)
    if source:
        stmt = stmt.where(CoinJoinCandidate.source == source)
    rows = list(session.scalars(stmt))
    rows.sort(key=lambda c: (-c.score, c.transaction_id))
    total = len(rows)
    items = [{
        "transaction_id": c.transaction_id, "source": c.source, "input_count": c.input_count, "output_count": c.output_count,
        "equal_output_group_size": c.equal_output_group_size, "equal_output_value": c.equal_output_value,
        "score": c.score, "created_at": c.created_at,
    } for c in rows[offset: offset + limit]]
    return total, items
