from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..database import Database
from ..deps import get_db, get_session
from ..errors import AppError
from ..models import Transaction
from ..schemas import IngestResult, RejectedItem, TransactionIn, TransactionOut, TransactionPage
from ..services import queries
from ..services.ingest import ingest_payloads

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

MAX_BATCH = 5000


@router.get("", response_model=TransactionPage)
def list_transactions(
    session: Session = Depends(get_session),
    q: Annotated[str | None, Query(max_length=128, description="Matches transaction ID or either wallet")] = None,
    sender: Annotated[str | None, Query(max_length=128)] = None,
    receiver: Annotated[str | None, Query(max_length=128)] = None,
    wallet: Annotated[str | None, Query(max_length=128, description="Exact wallet, either side")] = None,
    min_amount: Annotated[float | None, Query(ge=0)] = None,
    max_amount: Annotated[float | None, Query(ge=0)] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source: Literal["synthetic", "real_bitcoin"] | None = None,
    sort: Literal["timestamp", "amount_btc", "sender_wallet", "receiver_wallet"] = "timestamp",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    clauses = queries.transaction_filters(
        q=q, sender=sender, receiver=receiver, wallet=wallet, min_amount=min_amount, max_amount=max_amount,
        start=_naive_utc(start), end=_naive_utc(end), source=source)
    total, rows = queries.list_transactions(session, clauses, sort=sort, order=order, limit=limit, offset=offset)
    return TransactionPage(total=total, limit=limit, offset=offset, items=[TransactionOut.model_validate(r) for r in rows])


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, session: Session = Depends(get_session)):
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise AppError(404, "not_found", f"No transaction {transaction_id!r}.")
    return tx


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionIn, db: Database = Depends(get_db)):
    """
    Persist one synthetic transaction (creating its wallets if new). Transactions posted here are
    always labelled source='synthetic'; real Bitcoin data only enters through a configured source.
    """
    with db.transaction() as session:
        outcome = ingest_payloads(session, [payload.model_dump()], "synthetic")
        if outcome.rejected:
            raise AppError(409, "rejected", outcome.rejected[0]["error"])
        if outcome.duplicates:
            raise AppError(409, "duplicate_transaction", "A transaction with this ID already exists.")
        tx = session.get(Transaction, outcome.transaction_ids[0])
        return TransactionOut.model_validate(tx)


@router.post("/batch", response_model=IngestResult)
def create_transactions_batch(payloads: Annotated[list[Any], Body(max_length=MAX_BATCH)], db: Database = Depends(get_db)):
    """Persist many synthetic transactions. Invalid items are reported by index; valid ones are still saved."""
    with db.transaction() as session:
        outcome = ingest_payloads(session, payloads, "synthetic")
    return IngestResult(
        inserted=outcome.inserted, duplicates=outcome.duplicates,
        rejected=[RejectedItem(**r) for r in outcome.rejected], transaction_ids=outcome.transaction_ids,
    )


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        from datetime import timezone

        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value

