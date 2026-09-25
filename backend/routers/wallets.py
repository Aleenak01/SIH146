from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import Database
from ..deps import get_db, get_session
from ..errors import AppError
from ..models import Wallet, WalletReview
from ..schemas import ReviewIn, ReviewOut, TransactionOut, TransactionPage, WalletAnalysis, WalletDetail, WalletPage
from ..services import case_links
from ..services import leads as leads_service
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


@router.get("/{address}/analysis", response_model=WalletAnalysis)
def wallet_analysis(address: str, session: Session = Depends(get_session)):
    """ML score, forensic findings, features and priority of any wallet in the latest analysis (a lead or not)."""
    analysis = leads_service.wallet_analysis(session, address)
    if analysis is None:
        raise AppError(404, "not_found", f"No wallet {address!r}.")
    return analysis


@router.get("/{address}/review", response_model=ReviewOut)
def get_review(address: str, session: Session = Depends(get_session)):
    """Workflow state of a wallet: Unreviewed, Under Review (set by an investigator) or Case Created (it is in a case)."""
    if session.get(Wallet, address) is None:
        raise AppError(404, "not_found", f"No wallet {address!r}.")
    cases = case_links.case_ids_for_wallets(session, [address])
    return ReviewOut(wallet_address=address, status=case_links.review_status(session, [address], cases)[address], case_ids=cases.get(address, []))


@router.put("/{address}/review", response_model=ReviewOut)
def set_review(address: str, payload: ReviewIn, db: Database = Depends(get_db)):
    """
    Mark a wallet 'Under Review' or back to 'Unreviewed'. This is an investigator's bookmark only: it changes no score
    and does not create a case (the status becomes 'Case Created' only when the wallet is added to a case).
    """
    with db.transaction() as s:
        if s.get(Wallet, address) is None:
            raise AppError(404, "not_found", f"No wallet {address!r}.")
        row = s.get(WalletReview, address)
        if payload.status == "Under Review":
            if row is None:
                s.add(WalletReview(wallet_address=address, status="Under Review"))
        elif row is not None:
            s.delete(row)
        s.flush()
        cases = case_links.case_ids_for_wallets(s, [address])
        return ReviewOut(wallet_address=address, status=case_links.review_status(s, [address], cases)[address], case_ids=cases.get(address, []))
