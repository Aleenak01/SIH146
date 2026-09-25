"""
Synthetic network observations in the database: generate the dataset CSV, import it, and observe new transactions.

Everything here is SYNTHETIC and only ever attached to source='synthetic' transactions. Real Bitcoin transactions
get no observations from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from sqlalchemy import Integer, cast, delete, func, insert, select
from sqlalchemy.orm import Session

from ..database import Database
from ..ingestion import synthetic_network as sn
from ..models import NetworkObservation, Transaction

_CHUNK = 500


@dataclass
class NetworkImportResult:
    file: str
    rows_in_file: int
    inserted: int
    skipped_existing: int
    rejected: list[dict] = field(default_factory=list)
    observations_total: int = 0


def _chunks(seq: Sequence, size: int = _CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def synthetic_transactions(session: Session) -> list[tuple[str, datetime, str, str]]:
    """(transaction_id, timestamp, sender, receiver) of all synthetic transactions, chronologically."""
    return [tuple(r) for r in session.execute(
        select(Transaction.transaction_id, Transaction.timestamp, Transaction.sender_wallet, Transaction.receiver_wallet)
        .where(Transaction.source == "synthetic").order_by(Transaction.timestamp, Transaction.transaction_id))]


def generate_csv(db: Database, path: Path, seed: int = sn.DEFAULT_SEED) -> int:
    """Write the deterministic observation dataset for the synthetic transactions in the database. Returns the row count."""
    with db.session() as s:
        txs = synthetic_transactions(s)
    rows = sn.generate_dataset(txs, seed)
    sn.write_csv(path, rows)
    return len(rows)


def import_csv(db: Database, path: Path, replace: bool = False) -> NetworkImportResult:
    """
    Load an observation CSV. Rows are refused (and reported, never fatal) unless they refer to an existing synthetic
    transaction, name one of its two wallets, and use a documentation-range IP. Safe to repeat.
    """
    try:
        rows = sn.read_csv(path)
    except (OSError, ValueError) as e:
        raise ValueError(f"Cannot read {path.name}: {e}") from e
    try:
        shown = path.resolve().relative_to(Path(__file__).resolve().parents[2]).as_posix()
    except ValueError:
        shown = path.name
    result = NetworkImportResult(file=shown, rows_in_file=len(rows), inserted=0, skipped_existing=0)

    with db.transaction() as s:
        if replace:
            s.execute(delete(NetworkObservation).where(NetworkObservation.transaction_id.in_(
                select(Transaction.transaction_id).where(Transaction.source == "synthetic"))))
        tx_parties = {}
        ids = sorted({r["transaction_id"] for r in rows})
        for part in _chunks(ids):
            for tid, snd, rcv, source in s.execute(select(Transaction.transaction_id, Transaction.sender_wallet, Transaction.receiver_wallet, Transaction.source).where(Transaction.transaction_id.in_(part))):
                tx_parties[tid] = (snd, rcv, source)
        existing: set[str] = set()
        for part in _chunks([r["observation_id"] for r in rows]):
            existing.update(s.scalars(select(NetworkObservation.observation_id).where(NetworkObservation.observation_id.in_(part))))

        good = []
        for line, r in enumerate(rows, start=2):
            parties = tx_parties.get(r["transaction_id"])
            error = None
            if parties is None:
                error = f"transaction {r['transaction_id']} does not exist"
            elif parties[2] != "synthetic":
                error = "synthetic observations can only be attached to synthetic transactions"
            elif r["wallet_address"] not in parties[:2]:
                error = f"wallet {r['wallet_address']} is not a party to transaction {r['transaction_id']}"
            elif not sn.is_documentation_ip(r["ip_address"]):
                error = f"{r['ip_address']} is not in a documentation range (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)"
            elif not r["is_synthetic"]:
                error = "row is not marked synthetic"
            if error:
                result.rejected.append({"line": line, "error": error})
            elif r["observation_id"] in existing:
                result.skipped_existing += 1
            else:
                good.append({k: r[k] for k in sn.CSV_COLUMNS})
        for part in _chunks(good):
            s.execute(insert(NetworkObservation), part)
        result.inserted = len(good)
        result.observations_total = s.scalar(select(func.count()).select_from(NetworkObservation)) or 0
    return result


def _load_state(session: Session, wallets: set[str], seed: int) -> sn.ObservationGenerator:
    """Rebuild the generator's knowledge (devices, IPs, sessions) for these wallets from stored observations."""
    gen = sn.ObservationGenerator(seed)
    N = NetworkObservation
    # each wallet's most recent device
    for w, dev, _ in session.execute(select(N.wallet_address, N.device_id, N.observed_at).where(N.wallet_address.in_(wallets)).order_by(N.observed_at.desc())):
        gen.wallet_device.setdefault(w, dev)
    device_ids = set(gen.wallet_device.values())
    for d in device_ids:
        ips = list(session.scalars(select(N.ip_address).where(N.device_id == d).group_by(N.ip_address).order_by(func.count().desc(), N.ip_address)))
        ua, nt, region = session.execute(select(N.user_agent, N.network_type, N.geo_region).where(N.device_id == d).limit(1)).one()
        gen.devices[d] = sn.Device(d, ips, ua, nt, region)
        last = session.execute(select(N.session_id, N.observed_at).where(N.device_id == d).order_by(N.observed_at.desc()).limit(1)).first()
        if last:
            gen.sessions[d] = sn._Session(last[0], last[1])
    highest = session.scalar(select(func.max(cast(func.substr(N.device_id, 5), Integer))).where(N.device_id.like("dev-%")))
    gen.next_device_no = (highest or 0) + 1
    gen.used_ips = set(session.scalars(select(N.ip_address).distinct()))
    return gen


def observe_new_transactions(session: Session, rows: Sequence[dict], seed: int = sn.DEFAULT_SEED) -> int:
    """
    Live hook: create synthetic observations for freshly inserted SYNTHETIC transactions, continuing each wallet's
    existing device (or giving a new wallet a new device). rows: dicts with transaction_id, timestamp,
    sender_wallet, receiver_wallet. Runs inside the caller's transaction. Returns the number of observations created.
    """
    if not rows:
        return 0
    wallets = {w for r in rows for w in (r["sender_wallet"], r["receiver_wallet"])}
    gen = _load_state(session, wallets, seed)
    out: list[dict] = []
    for r in sorted(rows, key=lambda r: (r["timestamp"], r["transaction_id"])):
        out.extend(gen.observe(r["transaction_id"], r["timestamp"], r["sender_wallet"], r["receiver_wallet"]))
    existing = set(session.scalars(select(NetworkObservation.observation_id).where(NetworkObservation.observation_id.in_([o["observation_id"] for o in out])))) if out else set()
    out = [o for o in out if o["observation_id"] not in existing]
    for part in _chunks(out):
        session.execute(insert(NetworkObservation), part)
    return len(out)
