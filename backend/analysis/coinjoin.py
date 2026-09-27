"""
CoinJoin-like transaction detection (Phase 3 Part A).

Heuristic: a transaction whose rich (address-level) data shows several DISTINCT input addresses (candidate: several
independent spenders pooled their coins into one transaction) together with several outputs of about the same value
(candidate: a common "denomination" every participant receives back) looks like a CoinJoin-style transaction. This
is a heuristic candidate flag, never a certainty -- an ordinary transaction can occasionally match by chance (for
example, several equal payments to different people in one batch).

Used by analysis/entities.py: a transaction flagged here is excluded from the common-input-ownership union-find, so
that pooling several people's inputs together (the whole point of a CoinJoin) is not read as evidence that they are
all one entity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, select

from ..database import Database
from ..models import CoinJoinCandidate, TxDetails, TxInput, TxOutput
from ..services.ingest import WRITE_LOCK

DEFAULT_MIN_INPUTS = 3
DEFAULT_MIN_EQUAL_OUTPUTS = 3
DEFAULT_EQUAL_VALUE_TOLERANCE = 0.01     # two output amounts count as "equal" within this fraction of each other
NOTE = ("Heuristic candidate: several distinct input addresses spent together with several outputs of about the "
        "same value. This looks like a CoinJoin-style transaction; it is not a certainty, and ordinary transactions "
        "can occasionally match by chance (for example, several equal payments to different people).")


@dataclass(frozen=True)
class CoinJoinMetrics:
    input_count: int             # distinct input addresses
    output_count: int            # distinct output addresses
    equal_output_group_size: int
    equal_output_value: float
    score: float                 # 0-1: (share of outputs in the equal group) x (how balanced inputs/outputs are)

    def qualifies(self, *, min_inputs: int, min_equal_outputs: int) -> bool:
        return self.input_count >= min_inputs and self.equal_output_group_size >= min_equal_outputs


def _largest_equal_group(amounts: list[float], tolerance: float) -> tuple[int, float]:
    """
    The largest group of output amounts that are all within `tolerance` (relative) of the group's lowest member.
    Amounts are sorted and scanned once with a sliding reference value -- enough for the "denomination" pattern a
    CoinJoin produces (many outputs clustered tightly around one value), without a full clustering algorithm.
    """
    if not amounts:
        return 0, 0.0
    ordered = sorted(amounts)
    best_size, best_value = 1, ordered[0]
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] <= ordered[i] * (1 + tolerance):
            j += 1
        size = j - i + 1
        if size > best_size:
            best_size, best_value = size, ordered[i]
        i = j + 1
    return best_size, best_value


def analyse_transaction(input_addresses: Iterable[str], output_amounts: Iterable[float], *,
                        tolerance: float = DEFAULT_EQUAL_VALUE_TOLERANCE) -> CoinJoinMetrics:
    """Pure computation from one transaction's rich data. `score` is a simple heuristic confidence, not a probability:
    the share of outputs that fall in the largest equal-value group, scaled down when inputs and outputs are very
    unbalanced in count (a real CoinJoin usually has a comparable number of participants on each side)."""
    distinct_inputs = len(set(input_addresses))
    amounts = list(output_amounts)
    output_count = len(amounts)
    group_size, group_value = _largest_equal_group(amounts, tolerance)
    if output_count == 0:
        score = 0.0
    else:
        balance = min(1.0, distinct_inputs / max(distinct_inputs, output_count, 1))
        score = round((group_size / output_count) * balance, 4)
    return CoinJoinMetrics(input_count=distinct_inputs, output_count=output_count, equal_output_group_size=group_size,
                           equal_output_value=round(group_value, 8), score=score)


# ---- persistence ----------------------------------------------------------------------------------------------
@dataclass
class CoinJoinRefresh:
    candidates: int = 0
    examined: int = 0


def refresh_coinjoin(db: Database, source: str = "synthetic", *, min_inputs: int = DEFAULT_MIN_INPUTS,
                     min_equal_outputs: int = DEFAULT_MIN_EQUAL_OUTPUTS, tolerance: float = DEFAULT_EQUAL_VALUE_TOLERANCE) -> CoinJoinRefresh:
    """Recompute CoinJoin-like candidates for one source's rich (address-level) data and replace what was stored."""
    with db.session() as s:
        tx_ids = list(s.scalars(select(TxDetails.transaction_id).where(TxDetails.source == source)))
        if not tx_ids:
            return CoinJoinRefresh()
        in_rows = list(s.execute(select(TxInput.transaction_id, TxInput.address).where(TxInput.transaction_id.in_(tx_ids))))
        out_rows = list(s.execute(select(TxOutput.transaction_id, TxOutput.amount_btc).where(TxOutput.transaction_id.in_(tx_ids))))

    in_by_tx: dict[str, list[str]] = {}
    for tid, addr in in_rows:
        in_by_tx.setdefault(tid, []).append(addr)
    out_by_tx: dict[str, list[float]] = {}
    for tid, amt in out_rows:
        out_by_tx.setdefault(tid, []).append(amt)

    candidates: list[CoinJoinCandidate] = []
    for tid in sorted(tx_ids):
        m = analyse_transaction(in_by_tx.get(tid, []), out_by_tx.get(tid, []), tolerance=tolerance)
        if m.qualifies(min_inputs=min_inputs, min_equal_outputs=min_equal_outputs):
            candidates.append(CoinJoinCandidate(transaction_id=tid, source=source, input_count=m.input_count, output_count=m.output_count,
                                                equal_output_group_size=m.equal_output_group_size, equal_output_value=m.equal_output_value, score=m.score))

    with WRITE_LOCK, db.transaction() as s:
        s.execute(delete(CoinJoinCandidate).where(CoinJoinCandidate.source == source))
        s.flush()
        for c in candidates:
            s.add(c)
    return CoinJoinRefresh(candidates=len(candidates), examined=len(tx_ids))
