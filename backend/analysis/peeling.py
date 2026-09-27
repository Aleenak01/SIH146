"""
Peeling-chain detection (Phase 3 Part A).

A peeling chain is a sequence of wallet-level transfers A0 -> A1 -> A2 -> ... -> An (n >= `min_hops` hops) where each
hop forwards most of what the wallet just received, in one transaction, to the next wallet -- the classic Bitcoin
forensics "peeling chain" pattern (a large balance is walked down a chain of wallets, peeling off a small amount as
"change" at each stop).

Built entirely from the existing flat `transactions` table (the same sender/receiver/amount/timestamp data the
wallet-level relationship graph and transaction table already use) -- no rich address-level data is needed.

Rule, precisely
    Each wallet is walked through its own timeline of incoming and outgoing transfers, in chronological order. A
    wallet's most recently received transfer is "consumed" by whichever outgoing transfer comes right after it --
    whether or not that transfer qualifies -- so only the FIRST outgoing transfer after a receipt is ever considered
    a candidate continuation of the chain. It qualifies (continues the chain) if it forwards at least
    `dominance_share` of what was just received. This keeps detection deterministic (at most one predecessor and one
    successor per transaction, so the chains are disjoint, non-overlapping paths) instead of searching every later
    transfer for the best-matching ratio, which would be exponential and produce many overlapping candidate chains.

    A chain is reported only once it reaches `min_hops` transfers; its id is 'PEEL-<its first transaction's id>'
    (stable across re-runs, since the same transaction can only ever be a chain's root once).

This is a heuristic pattern: an investigative signal that money moved in a chain-like way, never proof of layering,
mixing or laundering. A legitimate merchant forwarding funds, or a wallet-management tool sweeping a balance, can
produce the same shape.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import delete, select

from ..database import Database
from ..models import PeelingChain, PeelingChainHop, Transaction
from ..services.ingest import WRITE_LOCK

DEFAULT_DOMINANCE_SHARE = 0.85
DEFAULT_MIN_HOPS = 3
ID_PREFIX = "PEEL"
NOTE = ("Heuristic pattern: each hop forwards most of what it just received, in one transaction, to the next "
        "wallet. This is an investigative signal that funds moved in a chain-like way, never proof of layering, "
        "mixing or laundering.")


@dataclass(frozen=True)
class PeelHop:
    transaction_id: str
    from_wallet: str
    to_wallet: str
    amount_btc: float


@dataclass(frozen=True)
class PeelChain:
    hops: tuple[PeelHop, ...]

    @property
    def chain_id(self) -> str:
        return f"{ID_PREFIX}-{self.hops[0].transaction_id}"

    @property
    def start_wallet(self) -> str:
        return self.hops[0].from_wallet

    @property
    def end_wallet(self) -> str:
        return self.hops[-1].to_wallet

    @property
    def hop_count(self) -> int:
        return len(self.hops)


# ---- pure computation (framework-independent) --------------------------------------------------------------------
def compute_peeling_chains(transfers: Iterable[tuple[str, str, str, float, datetime]], *,
                           dominance_share: float = DEFAULT_DOMINANCE_SHARE, min_hops: int = DEFAULT_MIN_HOPS) -> list[PeelChain]:
    """
    transfers: (transaction_id, sender_wallet, receiver_wallet, amount_btc, timestamp), any order, transaction ids
    unique. Deterministic: the same input always produces the same chains, regardless of input order.
    """
    rows = list(transfers)
    by_id = {tid: (tid, snd, rcv, amt, ts) for tid, snd, rcv, amt, ts in rows}
    events: dict[str, list[tuple[datetime, str, str, float]]] = defaultdict(list)   # wallet -> [(ts, 'in'|'out', tx_id, amount)]
    for tid, snd, rcv, amt, ts in rows:
        events[snd].append((ts, "out", tid, amt))
        events[rcv].append((ts, "in", tid, amt))

    predecessor: dict[str, str] = {}    # tx_id -> the tx_id it continues (its chain predecessor)
    for evs in events.values():
        evs.sort(key=lambda e: (e[0], e[2]))          # chronological; stable tie-break by transaction id
        last_received: tuple[str, float] | None = None
        for _ts, kind, tid, amount in evs:
            if kind == "in":
                last_received = (tid, amount)
            else:
                if last_received is not None:
                    recv_tx, recv_amount = last_received
                    if recv_amount > 0 and amount >= dominance_share * recv_amount:
                        predecessor[tid] = recv_tx
                last_received = None      # consumed regardless of whether it qualified: only the next spend counts

    successor = {p: t for t, p in predecessor.items()}     # injective by construction: at most one successor per tx

    chains: list[PeelChain] = []
    roots = sorted(tid for tid in by_id if tid not in predecessor)
    for root in roots:
        chain_ids = [root]
        visited = {root}
        cur = root
        while cur in successor:
            nxt = successor[cur]
            if nxt in visited:      # defensive: provably impossible (hop timestamps are non-decreasing along a
                break               # chain), but never loop forever on a pathological input
            visited.add(nxt)
            chain_ids.append(nxt)
            cur = nxt
        if len(chain_ids) >= min_hops:
            hops = tuple(PeelHop(transaction_id=tid, from_wallet=by_id[tid][1], to_wallet=by_id[tid][2], amount_btc=by_id[tid][3]) for tid in chain_ids)
            chains.append(PeelChain(hops=hops))
    return chains


# ---- persistence ----------------------------------------------------------------------------------------------
@dataclass
class PeelingRefresh:
    chains: int = 0
    hops: int = 0
    longest_chain: int = 0


def refresh_peeling(db: Database, source: str = "synthetic", *, dominance_share: float = DEFAULT_DOMINANCE_SHARE,
                    min_hops: int = DEFAULT_MIN_HOPS) -> PeelingRefresh:
    """Recompute peeling chains for one source's transfers and replace what was stored (idempotent, deterministic ids)."""
    with db.session() as s:
        rows = [tuple(r) for r in s.execute(select(
            Transaction.transaction_id, Transaction.sender_wallet, Transaction.receiver_wallet, Transaction.amount_btc, Transaction.timestamp,
        ).where(Transaction.source == source))]
    chains = compute_peeling_chains(rows, dominance_share=dominance_share, min_hops=min_hops)

    with WRITE_LOCK, db.transaction() as s:
        old_ids = select(PeelingChain.chain_id).where(PeelingChain.source == source)
        s.execute(delete(PeelingChainHop).where(PeelingChainHop.chain_id.in_(old_ids)))
        s.execute(delete(PeelingChain).where(PeelingChain.source == source))
        s.flush()
        for chain in chains:
            s.add(PeelingChain(chain_id=chain.chain_id, source=source, start_wallet=chain.start_wallet, end_wallet=chain.end_wallet,
                               hop_count=chain.hop_count, total_btc_start=chain.hops[0].amount_btc, total_btc_end=chain.hops[-1].amount_btc))
            s.flush()
            for i, hop in enumerate(chain.hops, start=1):
                s.add(PeelingChainHop(chain_id=chain.chain_id, hop_index=i, from_wallet=hop.from_wallet, to_wallet=hop.to_wallet,
                                      transaction_id=hop.transaction_id, amount_btc=hop.amount_btc))
    return PeelingRefresh(chains=len(chains), hops=sum(c.hop_count for c in chains), longest_chain=max((c.hop_count for c in chains), default=0))
