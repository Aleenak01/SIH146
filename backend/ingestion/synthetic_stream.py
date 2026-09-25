"""
SYNTHETIC transaction stream for demonstrating continuous monitoring. It is not real data and not a real network.

New transfers are drawn from the behaviour already present in the database: a wallet is chosen in proportion to its
history, it sends or receives with a partner it has used before (85%) or a random other wallet (15%), and the
amount follows that wallet's own typical amount (log-normal, like the original generator's normal activity). Nothing
is labelled suspicious. A seeded random generator makes a demo repeatable.

`fan_out_scenario()` is an OPTIONAL, explicitly labelled demo scenario: one previously quiet wallet sends to many
wallets it never used within minutes. Its transactions are ordinary synthetic transactions (recognisable only by a
'scenario' marker in `source_ref`, which the analysis never reads), so the existing model and rules judge them
exactly as they would any other data.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..database import Database
from ..models import Transaction, Wallet
from ..schemas import TransactionIn
from .base import SourcedTransaction

_IN_COUNTS, _IN_WEIGHTS = [1, 2, 3, 4], [0.55, 0.25, 0.13, 0.07]        # same distributions as generate_dataset.normal_counts()
_OUT_COUNTS, _OUT_WEIGHTS = [1, 2, 3], [0.30, 0.55, 0.15]


@dataclass
class _Profile:
    address: str
    tx_count: int = 0
    amount_sum: float = 0.0
    partners: dict[str, int] = field(default_factory=dict)

    @property
    def mean_amount(self) -> float:
        return self.amount_sum / self.tx_count if self.tx_count else 0.05


class StreamUnavailable(RuntimeError):
    """The database does not hold enough synthetic data to base a stream on."""


class SyntheticStreamSource:
    source = "synthetic"

    def __init__(self, db: Database, seed: int = 146) -> None:
        self.db = db
        self.rng = random.Random(seed)
        self.token = int(time.time())          # makes source_refs unique across restarts
        self.seq = 0
        self._profiles: dict[str, _Profile] = {}
        self._addresses: list[str] = []
        self._weights: list[int] = []
        self.refresh()

    # ---- profiles --------------------------------------------------------------------------------------
    def refresh(self) -> None:
        with self.db.session() as s:
            addresses = list(s.scalars(select(Wallet.address).where(Wallet.source == "synthetic").order_by(Wallet.address)))
            profiles = {a: _Profile(a) for a in addresses}
            for snd, rcv, n, total in s.execute(
                select(Transaction.sender_wallet, Transaction.receiver_wallet, func.count(), func.sum(Transaction.amount_btc))
                .where(Transaction.source == "synthetic").group_by(Transaction.sender_wallet, Transaction.receiver_wallet)
            ):
                for me, other in ((snd, rcv), (rcv, snd)):
                    p = profiles.get(me)
                    if p is not None:
                        p.tx_count += n
                        p.amount_sum += total
                        p.partners[other] = p.partners.get(other, 0) + n
        if len(addresses) < 3:
            raise StreamUnavailable("The synthetic stream needs existing synthetic wallets; import the dataset first.")
        self._profiles, self._addresses = profiles, addresses
        self._weights = [max(1, profiles[a].tx_count) for a in addresses]

    # ---- generation -------------------------------------------------------------------------------------
    def _counts(self) -> tuple[int, int]:
        return self.rng.choices(_IN_COUNTS, _IN_WEIGHTS)[0], self.rng.choices(_OUT_COUNTS, _OUT_WEIGHTS)[0]

    def _make(self, ts: datetime, sender: str, receiver: str, amount: float, ref: str) -> SourcedTransaction:
        n_in, n_out = self._counts()
        tx = TransactionIn(timestamp=ts, sender_wallet=sender, receiver_wallet=receiver, amount_btc=max(round(amount, 8), 0.00001),
                           input_count=n_in, output_count=n_out)
        return SourcedTransaction(tx, ref)

    def generate(self, n: int, now: datetime) -> list[SourcedTransaction]:
        out = []
        for _ in range(n):
            w = self._profiles[self.rng.choices(self._addresses, self._weights)[0]]
            if w.partners and self.rng.random() < 0.85:
                partner = self.rng.choices(list(w.partners), list(w.partners.values()))[0]
            else:
                partner = self.rng.choice([a for a in self._addresses if a != w.address])
            amount = w.mean_amount * self.rng.lognormvariate(0, 0.35)
            sender, receiver = (w.address, partner) if self.rng.random() < 0.5 else (partner, w.address)
            self.seq += 1
            out.append(self._make(now - timedelta(seconds=self.rng.uniform(0, 5)), sender, receiver, amount, f"stream:{self.token}:{self.seq}"))
        return out

    def fan_out_scenario(self, now: datetime, receivers: int = 15) -> tuple[str, list[SourcedTransaction]]:
        """Demo scenario: one quiet wallet sends to many wallets it has never used, within a few minutes."""
        # "Quiet" is relative to the population: wallets in the lowest-activity quartile that already have the 2+
        # transactions needed to be scored.
        counts = sorted(p.tx_count for p in self._profiles.values() if p.tx_count >= 2)
        cutoff = counts[len(counts) // 4] if counts else 0
        quiet = [a for a in self._addresses if 2 <= self._profiles[a].tx_count <= cutoff]
        if not quiet:
            raise StreamUnavailable("No quiet wallet available for the demo scenario.")
        wallet = self.rng.choice(quiet)
        prof = self._profiles[wallet]
        candidates = [a for a in self._addresses if a != wallet and a not in prof.partners]
        targets = self.rng.sample(candidates, min(receivers, len(candidates)))
        # Timestamps walk backwards from `now` with 20-180 s gaps, so the burst is over within minutes and never in the future.
        times, t = [], now
        for _ in targets:
            times.append(t)
            t -= timedelta(seconds=self.rng.uniform(20, 180))
        txs = []
        for target, ts in zip(targets, sorted(times)):
            self.seq += 1
            txs.append(self._make(ts, wallet, target, prof.mean_amount * self.rng.uniform(0.8, 1.2),
                                  f"stream:{self.token}:scenario-fan-out:{self.seq}"))
        return wallet, txs
