"""
Confidence score (Phase 4 Part A): a second, explainable score per wallet, shown ALONGSIDE the existing
combined_score, never replacing it.

combined_score (ml/result_fusion.py, kept as is) blends only the ML anomaly score and the forensic rule count. It
says nothing about corroborating signals from the later phases: whether other wallets sharing this wallet's
common-input-ownership address entity (Phase 2) are themselves flagged, whether that entity has any recorded
network-correlation finding (Phase 2), or whether this wallet turned up in a peeling chain or a CoinJoin-like
transaction (Phase 3). confidence_score folds those in, each as its own named, explainable contribution -- the same
spirit as the forensic rules ("why this wallet was flagged"), extended across phases.

    confidence_score = BASE_WEIGHT * combined_score
                      + ENTITY_WEIGHT        (if its address entity also contains another flagged/lead wallet)
                      + CORRELATION_WEIGHT   (if its address entity has a recorded correlation finding)
                      + PATTERN_WEIGHT       (if it appears in a peeling chain or a CoinJoin-like transaction)

BASE_WEIGHT + ENTITY_WEIGHT + CORRELATION_WEIGHT + PATTERN_WEIGHT = 1.0, so the score stays in [0, 1] the same way
combined_score does. The three boost weights are each all-or-nothing (the signal either applies or it does not),
kept simple so every point of the score traces back to one named, human-readable reason -- same style as
fusion.py's HIGH_SHARE/MEDIUM_SHARE constants: a prototype configuration, chosen for explainability, NOT
statistically validated.

Deliberately NOT folded in: on-demand risk propagation (Phase 3). That is an investigator tool computed per query
from chosen seed wallets, with no fixed "is this wallet risky" answer of its own -- it stays a separate, unstored,
on-demand feature.

This module reads combined_score and wallet identity (sender_wallet/receiver_wallet) freely: unlike entities.py /
correlation.py / coinjoin.py, it is NOT part of the blind, address-only entity-building step, so it has none of
their restrictions on reading wallet-identifying columns. It reuses entity_queries.linked_wallets_for_entities
(the same read-only, presentational join Phase 3 Part B introduced) rather than re-deriving wallet<->entity
membership a third time.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from ..database import Database
from ..models import (
    AddressEntity, ConfidenceScore, ConfidenceSignal, CoinJoinCandidate, CorrelationFinding, FusionResult,
    InvestigativeLead, PeelingChain, PeelingChainHop, Transaction,
)
from ..services.entity_queries import linked_wallets_for_entities
from ..services.ingest import WRITE_LOCK
from .service import latest_run

BASE_WEIGHT = 0.55          # the existing combined_score, carried through unchanged
ENTITY_WEIGHT = 0.15        # its address entity also contains another flagged/lead wallet
CORRELATION_WEIGHT = 0.10   # its address entity has a recorded network-correlation finding
PATTERN_WEIGHT = 0.20       # it appears in a peeling chain or a CoinJoin-like transaction
CONFIDENCE_LABEL = "Prototype explainable-confidence weighting (base 55% / entity 15% / correlation 10% / pattern 20%) - not statistically validated."

SIGNAL_LABEL = {
    "combined_score": "Existing combined score (ML + forensic rules).",
    "entity_co_flagged": "Its common-input-ownership address entity also contains another flagged/lead wallet.",
    "correlation_findings": "Its address entity has a recorded network-correlation finding.",
    "pattern_involvement": "Appears in a peeling chain or a CoinJoin-like transaction.",
}


@dataclass(frozen=True)
class Signal:
    name: str
    contribution: float
    detail: str


@dataclass(frozen=True)
class WalletConfidence:
    wallet_address: str
    score: float
    signals: tuple[Signal, ...]


def compute_wallet_confidence(wallet_address: str, *, combined_score: float, other_flagged_in_entity: int,
                              correlation_finding_count: int, in_pattern: bool) -> WalletConfidence:
    """Pure computation: given the four inputs (already looked up for this wallet), the score and its signals."""
    base = round(BASE_WEIGHT * combined_score, 6)
    signals = [Signal("combined_score", base, f"{SIGNAL_LABEL['combined_score']} {BASE_WEIGHT:.0%} weight on a combined score of {combined_score:.3f}.")]
    score = base
    if other_flagged_in_entity > 0:
        signals.append(Signal("entity_co_flagged", ENTITY_WEIGHT,
                              f"{SIGNAL_LABEL['entity_co_flagged']} {other_flagged_in_entity} other flagged/lead wallet(s) share it."))
        score += ENTITY_WEIGHT
    if correlation_finding_count > 0:
        signals.append(Signal("correlation_findings", CORRELATION_WEIGHT,
                              f"{SIGNAL_LABEL['correlation_findings']} {correlation_finding_count} finding(s) recorded."))
        score += CORRELATION_WEIGHT
    if in_pattern:
        signals.append(Signal("pattern_involvement", PATTERN_WEIGHT, SIGNAL_LABEL["pattern_involvement"]))
        score += PATTERN_WEIGHT
    return WalletConfidence(wallet_address=wallet_address, score=round(min(score, 1.0), 6), signals=tuple(signals))


# ---- bulk lookups used to feed the pure computation above, one pass over the whole source ------------------------
def _entity_ids_by_wallet(session, source: str) -> dict[str, set[str]]:
    """{wallet: {entity_id, ...}} -- the address entities this wallet's own spending touches. Derived by inverting
    linked_wallets_for_entities (entity -> wallets that sent a tx it spent from), which is the same relation viewed
    from the other side; see entity_queries.py for the join itself."""
    entity_ids = list(session.scalars(select(AddressEntity.entity_id).where(AddressEntity.source == source)))
    linked = linked_wallets_for_entities(session, entity_ids)
    out: dict[str, set[str]] = defaultdict(set)
    for eid, wallets in linked.items():
        for w in wallets:
            out[w].add(eid)
    return out


def _wallets_in_patterns(session, source: str) -> set[str]:
    """Wallets appearing in any peeling-chain hop, or sender/receiver of a CoinJoin-flagged transaction."""
    hop_wallets: set[str] = set()
    rows = session.execute(
        select(PeelingChainHop.from_wallet, PeelingChainHop.to_wallet)
        .join(PeelingChain, PeelingChain.chain_id == PeelingChainHop.chain_id)
        .where(PeelingChain.source == source)
    ).all()
    for frm, to in rows:
        hop_wallets.add(frm)
        hop_wallets.add(to)

    coinjoin_tx_ids = select(CoinJoinCandidate.transaction_id).where(CoinJoinCandidate.source == source)
    cj_rows = session.execute(select(Transaction.sender_wallet, Transaction.receiver_wallet).where(Transaction.transaction_id.in_(coinjoin_tx_ids))).all()
    for snd, rcv in cj_rows:
        hop_wallets.add(snd)
        hop_wallets.add(rcv)
    return hop_wallets


def _wallets_with_lead(session, source: str) -> set[str]:
    return set(session.scalars(select(InvestigativeLead.wallet_address).where(InvestigativeLead.source == source)))


def _correlation_finding_counts(session, source: str) -> dict[str, int]:
    """{entity_id: number of correlation findings recorded for it}."""
    rows = session.execute(select(CorrelationFinding.entity_id, func.count()).where(CorrelationFinding.source == source).group_by(CorrelationFinding.entity_id)).all()
    return dict(rows)


@dataclass
class ConfidenceRefresh:
    wallets: int = 0
    analysis_available: bool = True


def compute_all(session, source: str) -> list[WalletConfidence]:
    """Every scored wallet (latest completed analysis run) of one source, with its confidence score and signals."""
    run = latest_run(session, source)
    if run is None:
        return []
    fusion = {f.wallet_address: f.combined_score for f in session.scalars(select(FusionResult).where(FusionResult.run_id == run.run_id))}
    if not fusion:
        return []

    entities_by_wallet = _entity_ids_by_wallet(session, source)
    lead_wallets = _wallets_with_lead(session, source)
    finding_counts = _correlation_finding_counts(session, source)
    pattern_wallets = _wallets_in_patterns(session, source)

    results = []
    for wallet, combined_score in fusion.items():
        entity_ids = entities_by_wallet.get(wallet, set())
        other_flagged = 0
        if entity_ids:
            linked = linked_wallets_for_entities(session, list(entity_ids))
            other_flagged = len({w for ws in linked.values() for w in ws if w != wallet} & lead_wallets)
        correlation_count = sum(finding_counts.get(eid, 0) for eid in entity_ids)
        in_pattern = wallet in pattern_wallets
        results.append(compute_wallet_confidence(
            wallet, combined_score=combined_score, other_flagged_in_entity=other_flagged,
            correlation_finding_count=correlation_count, in_pattern=in_pattern,
        ))
    return results


def refresh_confidence(db: Database, source: str = "synthetic") -> ConfidenceRefresh:
    """Idempotent, like the other Phase 2/3 refresh_* steps: replaces what was stored for this source."""
    with db.session() as s:
        confidences = compute_all(s, source)
    if not confidences:
        with db.session() as s:
            has_run = latest_run(s, source) is not None
        return ConfidenceRefresh(wallets=0, analysis_available=has_run)

    with WRITE_LOCK, db.transaction() as s:
        wallet_ids = select(ConfidenceScore.wallet_address).where(ConfidenceScore.source == source)
        s.execute(delete(ConfidenceSignal).where(ConfidenceSignal.wallet_address.in_(wallet_ids)))
        s.execute(delete(ConfidenceScore).where(ConfidenceScore.source == source))
        s.flush()
        for c in confidences:
            s.add(ConfidenceScore(wallet_address=c.wallet_address, source=source, score=c.score))
            for sig in c.signals:
                s.add(ConfidenceSignal(wallet_address=c.wallet_address, source=source, signal_name=sig.name,
                                       contribution=sig.contribution, detail=sig.detail))
    return ConfidenceRefresh(wallets=len(confidences), analysis_available=True)
