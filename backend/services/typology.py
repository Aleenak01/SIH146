"""
Typology tags (Phase 4 Part A): a small, human-readable summary of which later-phase signals apply to one wallet,
synthesized live from data that already exists (address_entities, correlation_findings, peeling_chain_hops,
coinjoin_candidates). No new table -- this is a read-only view, recomputed on every call, not a stored analysis
step like confidence.py.

Like confidence.py, this reads wallet identity (sender_wallet/receiver_wallet) freely: it is not part of the
blind, address-only entity-building step, so none of entities.py's/correlation.py's leak-field restrictions apply.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CoinJoinCandidate, CorrelationFinding, PeelingChain, PeelingChainHop, Transaction, Wallet
from .entity_queries import entity_ids_for_wallet

TAG_PEELING_CHAIN = "Peeling chain"
TAG_COINJOIN = "Possible CoinJoin"
TAG_CORRELATED_ENTITY = "Correlated entity"


def wallet_typology(session: Session, wallet: str) -> dict[str, Any] | None:
    """{"wallet_address", "tags": [{"tag", "reason"}]}, or None if the wallet does not exist."""
    w = session.get(Wallet, wallet)
    if w is None:
        return None
    source = w.source
    tags: list[dict[str, str]] = []

    hop = session.execute(
        select(PeelingChainHop.chain_id)
        .join(PeelingChain, PeelingChain.chain_id == PeelingChainHop.chain_id)
        .where(PeelingChain.source == source, (PeelingChainHop.from_wallet == wallet) | (PeelingChainHop.to_wallet == wallet))
        .limit(1)
    ).first()
    if hop is not None:
        tags.append({"tag": TAG_PEELING_CHAIN, "reason": f"This wallet appears in detected peeling chain {hop[0]} (heuristic, not proof of layering)."})

    coinjoin_tx_ids = select(CoinJoinCandidate.transaction_id).where(CoinJoinCandidate.source == source)
    cj_tx = session.execute(
        select(Transaction.transaction_id).where(
            Transaction.transaction_id.in_(coinjoin_tx_ids), (Transaction.sender_wallet == wallet) | (Transaction.receiver_wallet == wallet),
        ).limit(1)
    ).first()
    if cj_tx is not None:
        tags.append({"tag": TAG_COINJOIN, "reason": f"Transaction {cj_tx[0]} involving this wallet is a candidate CoinJoin-like transaction (heuristic, never a certainty)."})

    entity_ids = entity_ids_for_wallet(session, wallet)
    if entity_ids:
        count = session.scalar(select(func.count()).select_from(CorrelationFinding).where(CorrelationFinding.entity_id.in_(entity_ids))) or 0
        if count > 0:
            tags.append({"tag": TAG_CORRELATED_ENTITY,
                        "reason": f"Its address entity ({', '.join(sorted(entity_ids))}) has {count} recorded network-correlation finding(s) (evidence, never a verdict)."})

    return {"wallet_address": wallet, "tags": tags,
            "note": "Synthesized live from the Phase 2/3 heuristic detectors. Each tag is an investigative signal, never proof of wrongdoing."}
