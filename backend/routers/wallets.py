from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..errors import AppError
from ..schemas import TransactionOut, TransactionPage, WalletDetail, WalletPage
from ..services import queries

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


@router.get("", response_model=WalletPage)
def list_wallets(
    session: Session = Depends(get_session),
    q: Annotated[str | None, Query(max_length=128)] = None,
    source: Literal["synthetic", "real_bitcoin"] | None = None,
    sort: Literal["address", "first_seen", "last_seen", "transaction_count", "total_received_btc", "total_sent_btc"] = "address",
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    total, items = queries.list_wallets(session, q=q, source=source, sort=sort, order=order, limit=limit, offset=offset)
    return WalletPage(total=total, limit=limit, offset=offset, items=items)


@router.get("/{address}", response_model=WalletDetail)
def get_wallet(address: str, session: Session = Depends(get_session)):
    wallet = queries.get_wallet(session, address)
    if wallet is None:
        raise AppError(404, "not_found", f"No wallet {address!r}.")
    return wallet


@router.get("/{address}/transactions", response_model=TransactionPage)
def wallet_transactions(
    address: str,
    session: Session = Depends(get_session),
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if queries.get_wallet(session, address) is None:
        raise AppError(404, "not_found", f"No wallet {address!r}.")
    clauses = queries.transaction_filters(wallet=address)
    total, rows = queries.list_transactions(session, clauses, sort="timestamp", order=order, limit=limit, offset=offset)
    return TransactionPage(total=total, limit=limit, offset=offset, items=[TransactionOut.model_validate(r) for r in rows])
