"""
On-demand risk propagation from seed wallets (Phase 3 Part A).

Given one or more seed wallets with an initial risk score of 1.0, this walks the SAME wallet-transfer graph already
used by the wallet-level relationship graph (services/graph.py's `wallet_neighbor_links`, also used by
GET /api/graph), and gives every reachable wallet a score that decays by a fixed factor per hop. This is an
on-demand investigator tool: nothing is stored, and calling it again with different seeds or settings simply
recomputes from scratch.

Multi-source breadth-first search: every seed starts at hop 0 with score 1.0. At each hop, every not-yet-reached
wallet linked (by any transfer, in either direction) to the current frontier is added at that hop distance, with

    propagated_score = decay_per_hop ** hop_distance

the same decay for every wallet at a given hop distance, since every seed starts equally at 1.0 -- there is no
notion of transfer amount or count changing the decay itself, only how many hops away a wallet is (the shortest
path from any seed). Where a wallet could be reached through more than one link at the same hop, the link with the
most transfers between the two wallets is used for its reported `path` (tie-break: lower wallet id), purely to make
the path deterministic and informative; it does not affect the score.

This is a heuristic investigator aid, not a validated risk score in any formal sense: it says only how many transfer
hops a wallet is from a chosen seed, never that the wallet did anything wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import Wallet
from ..services.graph import wallet_neighbor_links

DEFAULT_DECAY_PER_HOP = 0.5
DEFAULT_MAX_HOPS = 4
DEFAULT_MAX_NODES = 200
MAX_SEEDS = 50
NOTE = ("Heuristic investigator aid: the propagated score reflects only how many transfer hops a wallet is from a "
        "seed wallet, decayed by a fixed factor per hop. It is not a validated risk score and is not evidence of "
        "wrongdoing.")


@dataclass(frozen=True)
class RiskResult:
    wallet: str
    propagated_score: float
    hop_distance: int
    path: tuple[str, ...]


def propagate_risk(session: Session, seed_wallets: list[str], *, decay_per_hop: float = DEFAULT_DECAY_PER_HOP,
                   max_hops: int = DEFAULT_MAX_HOPS, max_nodes: int = DEFAULT_MAX_NODES) -> list[RiskResult]:
    seeds = list(dict.fromkeys(seed_wallets))       # de-duplicate, keep the caller's order
    if not seeds:
        raise AppError(422, "validation_error", "At least one seed wallet id is required.")
    if len(seeds) > MAX_SEEDS:
        raise AppError(422, "validation_error", f"At most {MAX_SEEDS} seed wallets are allowed in one call.")
    missing = [w for w in seeds if session.get(Wallet, w) is None]
    if missing:
        raise AppError(404, "not_found", f"Unknown wallet id(s): {', '.join(missing)}.")

    best: dict[str, RiskResult] = {w: RiskResult(wallet=w, propagated_score=1.0, hop_distance=0, path=(w,)) for w in seeds}
    known = set(seeds)
    frontier = set(seeds)
    for hop in range(1, max_hops + 1):
        if not frontier or len(known) >= max_nodes:
            break
        by_candidate = wallet_neighbor_links(session, frontier, known)
        score = decay_per_hop ** hop
        new_frontier: set[str] = set()
        # deterministic order: strongest link first, then wallet id (same tie-break style as elsewhere in the project)
        for candidate, per_parent in sorted(by_candidate.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
            if len(known) >= max_nodes:
                break
            parent = sorted(per_parent.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            best[candidate] = RiskResult(wallet=candidate, propagated_score=score, hop_distance=hop, path=best[parent].path + (candidate,))
            known.add(candidate)
            new_frontier.add(candidate)
        frontier = new_frontier

    return sorted(best.values(), key=lambda r: (-r.propagated_score, r.hop_distance, r.wallet))
