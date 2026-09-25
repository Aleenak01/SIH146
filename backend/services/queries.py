"""Read queries behind the transaction and wallet endpoints."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select, union
from sqlalchemy.orm import Session

from ..models import Transaction, Wallet
from ..schemas import WalletDetail, WalletOut

TX_SORTS = {
    "timestamp": Transaction.timestamp,
    "amount_btc": Transaction.amount_btc,
    "sender_wallet": Transaction.sender_wallet,
    "receiver_wallet": Transaction.receiver_wallet,
}


def transaction_filters(
    *, q: str | None = None, sender: str | None = None, receiver: str | None = None, wallet: str | None = None,
    min_amount: float | None = None, max_amount: float | None = None, start: datetime | None = None,
    end: datetime | None = None, source: str | None = None,
) -> list:
    """WHERE clauses shared by the list endpoint and (later) the search and export features."""
    clauses = []
    if q:
        clauses.append(or_(
            Transaction.transaction_id.contains(q, autoescape=True),
            Transaction.sender_wallet.contains(q, autoescape=True),
            Transaction.receiver_wallet.contains(q, autoescape=True),
        ))
    if sender:
        clauses.append(Transaction.sender_wallet.contains(sender, autoescape=True))
    if receiver:
        clauses.append(Transaction.receiver_wallet.contains(receiver, autoescape=True))
    if wallet:   # exact wallet, either side
        clauses.append(or_(Transaction.sender_wallet == wallet, Transaction.receiver_wallet == wallet))
    if min_amount is not None:
        clauses.append(Transaction.amount_btc >= min_amount)
    if max_amount is not None:
        clauses.append(Transaction.amount_btc <= max_amount)
    if start is not None:
        clauses.append(Transaction.timestamp >= start)
    if end is not None:
        clauses.append(Transaction.timestamp <= end)
    if source:
        clauses.append(Transaction.source == source)
    return clauses


def list_transactions(session: Session, clauses: list, *, sort: str, order: str, limit: int, offset: int):
    total = session.scalar(select(func.count()).select_from(Transaction).where(and_(*clauses))) if clauses else session.scalar(select(func.count()).select_from(Transaction))
    column = TX_SORTS[sort]
    ordering = column.desc() if order == "desc" else column.asc()
    stmt: Select = select(Transaction).where(and_(*clauses)) if clauses else select(Transaction)
    # Ties (same timestamp, same amount, ...) follow the sort direction so the order is stable and natural.
    tie = Transaction.transaction_id.desc() if order == "desc" else Transaction.transaction_id.asc()
    stmt = stmt.order_by(ordering, tie).limit(limit).offset(offset)
    return total or 0, list(session.scalars(stmt))


# ---- wallets -------------------------------------------------------------------------------
def _wallet_stats_query():
    """
    Wallet rows with in/out counts and BTC totals computed from the transactions table.
    Returns (select statement, {sort name: sortable expression}).
    """
    out = (
        select(Transaction.sender_wallet.label("w"), func.count().label("n"), func.sum(Transaction.amount_btc).label("btc"))
        .group_by(Transaction.sender_wallet).subquery()
    )
    inc = (
        select(Transaction.receiver_wallet.label("w"), func.count().label("n"), func.sum(Transaction.amount_btc).label("btc"))
        .group_by(Transaction.receiver_wallet).subquery()
    )
    in_n, out_n = func.coalesce(inc.c.n, 0), func.coalesce(out.c.n, 0)
    in_btc, out_btc = func.coalesce(inc.c.btc, 0.0), func.coalesce(out.c.btc, 0.0)
    stmt = (
        select(Wallet, in_n.label("incoming_count"), out_n.label("outgoing_count"),
               in_btc.label("total_received_btc"), out_btc.label("total_sent_btc"))
        .outerjoin(inc, inc.c.w == Wallet.address)
        .outerjoin(out, out.c.w == Wallet.address)
    )
    sorts = {
        "address": Wallet.address, "first_seen": Wallet.first_seen, "last_seen": Wallet.last_seen,
        "transaction_count": in_n + out_n, "total_received_btc": in_btc, "total_sent_btc": out_btc,
    }
    return stmt, sorts


WALLET_SORT_NAMES = ("address", "first_seen", "last_seen", "transaction_count", "total_received_btc", "total_sent_btc")


def _to_wallet_out(row) -> WalletOut:
    w: Wallet = row[0]
    return WalletOut(
        address=w.address, source=w.source, first_seen=w.first_seen, last_seen=w.last_seen,
        transaction_count=row.incoming_count + row.outgoing_count,
        incoming_count=row.incoming_count, outgoing_count=row.outgoing_count,
        total_received_btc=round(row.total_received_btc, 8), total_sent_btc=round(row.total_sent_btc, 8),
    )


def list_wallets(session: Session, *, q: str | None, source: str | None, sort: str, order: str, limit: int, offset: int):
    clauses = []
    if q:
        clauses.append(Wallet.address.contains(q, autoescape=True))
    if source:
        clauses.append(Wallet.source == source)

    stmt, sorts = _wallet_stats_query()
    count_stmt = select(func.count()).select_from(Wallet)
    if clauses:
        stmt, count_stmt = stmt.where(and_(*clauses)), count_stmt.where(and_(*clauses))
    col = sorts[sort]
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), Wallet.address.asc()).limit(limit).offset(offset)
    return session.scalar(count_stmt) or 0, [_to_wallet_out(r) for r in session.execute(stmt)]


def get_wallet(session: Session, address: str) -> WalletDetail | None:
    stmt, _ = _wallet_stats_query()
    row = session.execute(stmt.where(Wallet.address == address)).first()
    if row is None:
        return None
    base = _to_wallet_out(row)
    partners = union(
        select(Transaction.receiver_wallet.label("p")).where(Transaction.sender_wallet == address),
        select(Transaction.sender_wallet.label("p")).where(Transaction.receiver_wallet == address),
    ).subquery()
    n_partners = session.scalar(select(func.count()).select_from(partners)) or 0
    return WalletDetail(**base.model_dump(), unique_counterparties=n_partners)
