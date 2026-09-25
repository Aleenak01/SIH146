"""Import the existing synthetic CSV into the database (read-only on the CSV, idempotent)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from ..config import PROJECT_ROOT
from ..database import Database
from ..ingestion.synthetic_csv import SyntheticCSVSource
from ..models import Transaction, Wallet
from ..schemas import ImportResult
from .ingest import ingest
from .maintenance import purge_source


class ImportConflict(ValueError):
    """The database already holds different data for the same CSV rows."""


def import_synthetic_csv(db: Database, path: Path, replace: bool = False) -> ImportResult:
    """
    Load the CSV into `transactions` / `wallets`.

    - Running it again with the same file changes nothing (rows are matched by source_ref).
    - If the file now describes different transactions than the database holds (for example after
      regenerating the dataset), it refuses with ImportConflict unless replace=True, which first
      removes ALL existing synthetic data (transactions, wallets and derived results; cases stay).
    """
    items = SyntheticCSVSource(path).fetch()          # validates the file before anything is written

    with db.transaction() as session:
        replaced = False
        if replace:
            replaced = purge_source(session, "synthetic") > 0
        else:
            existing = {
                ref: (tid, ts, snd, rcv, round(amt, 8))
                for ref, tid, ts, snd, rcv, amt in session.execute(
                    select(Transaction.source_ref, Transaction.transaction_id, Transaction.timestamp, Transaction.sender_wallet,
                           Transaction.receiver_wallet, Transaction.amount_btc).where(
                        Transaction.source == "synthetic", Transaction.source_ref.like("row:%"))
                )
            }
            for it in items:
                prior = existing.get(it.source_ref)
                tx = it.transaction
                if prior is not None and prior != (tx.transaction_id, tx.timestamp, tx.sender_wallet, tx.receiver_wallet, tx.amount_btc):
                    raise ImportConflict(
                        f"The database already has different data for {it.source_ref}. The CSV has changed since it was "
                        "imported; re-run with replace=true to replace all synthetic data."
                    )

        outcome = ingest(session, items, "synthetic")
        if outcome.rejected:
            raise ImportConflict(f"{len(outcome.rejected)} transactions were refused, e.g. {outcome.rejected[0]['error']}")
        wallets_total = session.scalar(select(func.count()).select_from(Wallet).where(Wallet.source == "synthetic")) or 0

    try:
        shown = path.resolve().relative_to(PROJECT_ROOT).as_posix()     # never expose absolute local paths
    except ValueError:
        shown = path.name
    return ImportResult(
        source="synthetic", file=shown, transfers_in_file=len(items), inserted=outcome.inserted,
        skipped_existing=outcome.duplicates, wallets_total=wallets_total, replaced_existing=replaced,
    )
