"""
Ingestion core: validate -> deduplicate -> persist transactions -> create/update wallets.

Used by the CSV importer and by POST /api/transactions. Bad items are reported back, never raised
into the caller, so one malformed transaction cannot stop a batch (or later, the monitor).
Analysis triggering is added by the monitoring layer on top of this function.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from pydantic import ValidationError
from sqlalchemy import Integer, cast, func, insert, select
from sqlalchemy.orm import Session

from ..ingestion.base import SourcedTransaction
from ..models import SOURCES, Transaction, Wallet
from ..schemas import TransactionIn

# SQLite allows one writer at a time; this also keeps synthetic ID allocation race-free between the
# API and the background monitor.
WRITE_LOCK = threading.RLock()

SYNTHETIC_ID_PREFIX = "syn-"
_CHUNK = 500


@dataclass
class IngestOutcome:
    inserted: int = 0
    duplicates: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    transaction_ids: list[str] = field(default_factory=list)


def validate_payloads(payloads: Iterable[Any]) -> tuple[list[tuple[int, TransactionIn]], list[dict[str, Any]]]:
    """Validate raw dicts. Returns [(original_index, TransactionIn)] and the rejected items."""
    valid: list[tuple[int, TransactionIn]] = []
    rejected: list[dict[str, Any]] = []
    for i, payload in enumerate(payloads):
        try:
            valid.append((i, TransactionIn.model_validate(payload)))
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'item'}: {err['msg']}" for err in e.errors())
            rejected.append({"index": i, "error": msg})
    return valid, rejected


def _next_synthetic_number(session: Session) -> int:
    n = session.scalar(
        select(func.max(cast(func.substr(Transaction.transaction_id, len(SYNTHETIC_ID_PREFIX) + 1), Integer))).where(
            Transaction.source == "synthetic", Transaction.transaction_id.like(f"{SYNTHETIC_ID_PREFIX}%")
        )
    )
    return (n or 0) + 1


def _chunks(seq: Sequence, size: int = _CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def ingest(session: Session, items: Sequence[SourcedTransaction], source: str) -> IngestOutcome:
    """Persist already-validated transactions. Commits are left to the caller."""
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}")

    outcome = IngestOutcome()
    with WRITE_LOCK:
        # ---- assign / check identifiers ------------------------------------------------------
        next_no = _next_synthetic_number(session) if source == "synthetic" else 0
        candidates: list[tuple[int, str, SourcedTransaction]] = []
        for i, it in enumerate(items):
            tid = it.transaction.transaction_id
            if tid is None:
                if source != "synthetic":
                    outcome.rejected.append({"index": i, "error": "transaction_id is required for real_bitcoin transactions"})
                    continue
                tid = f"{SYNTHETIC_ID_PREFIX}{next_no:06d}"
                next_no += 1
            candidates.append((i, tid, it))

        ids = [c[1] for c in candidates]
        existing: dict[str, Transaction] = {}
        for part in _chunks(ids):
            for t in session.scalars(select(Transaction).where(Transaction.transaction_id.in_(part))):
                existing[t.transaction_id] = t
        refs = [c[2].source_ref for c in candidates if c[2].source_ref]
        existing_refs: set[str] = set()
        for part in _chunks(refs):
            existing_refs.update(session.scalars(select(Transaction.source_ref).where(Transaction.source == source, Transaction.source_ref.in_(part))))

        # ---- wallets already known (and which source they belong to) --------------------------
        addresses = sorted({a for _, _, it in candidates for a in (it.transaction.sender_wallet, it.transaction.receiver_wallet)})
        wallets: dict[str, Wallet] = {}
        for part in _chunks(addresses):
            for w in session.scalars(select(Wallet).where(Wallet.address.in_(part))):
                wallets[w.address] = w

        # ---- decide per item -------------------------------------------------------------------
        seen_ids: set[str] = set()
        seen_refs: set[str] = set()
        rows: list[dict[str, Any]] = []
        touched: dict[str, list] = {}           # address -> [min_ts, max_ts]
        for i, tid, it in candidates:
            tx = it.transaction
            prior = existing.get(tid)
            if prior is not None:
                same = (prior.timestamp, prior.sender_wallet, prior.receiver_wallet, round(prior.amount_btc, 8)) == (
                    tx.timestamp, tx.sender_wallet, tx.receiver_wallet, tx.amount_btc)
                if same:
                    outcome.duplicates += 1
                else:
                    outcome.rejected.append({"index": i, "error": f"transaction_id {tid} already exists with different content"})
                continue
            if tid in seen_ids or (it.source_ref and (it.source_ref in existing_refs or it.source_ref in seen_refs)):
                outcome.duplicates += 1
                continue
            conflict = next((a for a in (tx.sender_wallet, tx.receiver_wallet) if a in wallets and wallets[a].source != source), None)
            if conflict:
                outcome.rejected.append({"index": i, "error": f"wallet {conflict} belongs to a different data source"})
                continue

            seen_ids.add(tid)
            if it.source_ref:
                seen_refs.add(it.source_ref)
            rows.append({
                "transaction_id": tid, "timestamp": tx.timestamp, "sender_wallet": tx.sender_wallet,
                "receiver_wallet": tx.receiver_wallet, "amount_btc": tx.amount_btc,
                "input_count": tx.input_count, "output_count": tx.output_count,
                "source": source, "source_ref": it.source_ref,
            })
            for a in (tx.sender_wallet, tx.receiver_wallet):
                span = touched.setdefault(a, [tx.timestamp, tx.timestamp])
                span[0], span[1] = min(span[0], tx.timestamp), max(span[1], tx.timestamp)

        # ---- write wallets first (foreign keys), then transactions ----------------------------
        new_wallets = []
        for a, (first, last) in touched.items():
            w = wallets.get(a)
            if w is None:
                new_wallets.append({"address": a, "source": source, "first_seen": first, "last_seen": last})
            else:
                w.first_seen = first if w.first_seen is None else min(w.first_seen, first)
                w.last_seen = last if w.last_seen is None else max(w.last_seen, last)
        if new_wallets:
            session.execute(insert(Wallet), new_wallets)
        if rows:
            session.execute(insert(Transaction), rows)
        session.flush()

        outcome.inserted = len(rows)
        outcome.transaction_ids = [r["transaction_id"] for r in rows]
    return outcome


def ingest_payloads(session: Session, payloads: Sequence[Any], source: str = "synthetic") -> IngestOutcome:
    """
    Validate raw payloads (dicts) and ingest the valid ones. Rejected items are reported with the
    index they had in the request, whether they failed validation or were refused during ingest.
    """
    valid, rejected = validate_payloads(payloads)
    items = [SourcedTransaction(tx, None) for _, tx in valid]     # API items have no source_ref
    outcome = ingest(session, items, source)
    outcome.rejected = rejected + [{"index": valid[r["index"]][0], "error": r["error"]} for r in outcome.rejected]
    outcome.rejected.sort(key=lambda r: r["index"])
    return outcome
