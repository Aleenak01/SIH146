"""Which cases hold a wallet or cluster, and a wallet's review status. Kept free of other services to avoid import cycles."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CaseItem, WalletReview

REVIEW_STATUSES = ("Unreviewed", "Under Review", "Case Created")


def case_ids_for_wallets(session: Session, wallets: Iterable[str]) -> dict[str, list[str]]:
    """{wallet: [case ids]} for wallets that are a lead or wallet item of at least one case."""
    wallets = list(dict.fromkeys(wallets))
    out: dict[str, list[str]] = {}
    if not wallets:
        return out
    for wallet, case_id in session.execute(
        select(CaseItem.item_id, CaseItem.case_id).where(CaseItem.item_type.in_(("lead", "wallet")), CaseItem.item_id.in_(wallets)).order_by(CaseItem.case_id)
    ):
        ids = out.setdefault(wallet, [])
        if case_id not in ids:
            ids.append(case_id)
    return out


def case_ids_for_cluster(session: Session, cluster_id: str) -> list[str]:
    return list(session.scalars(select(CaseItem.case_id).where(CaseItem.item_type == "cluster", CaseItem.item_id == cluster_id).order_by(CaseItem.case_id)))


def review_status(session: Session, wallets: Iterable[str], case_map: dict[str, list[str]] | None = None) -> dict[str, str]:
    """
    Unreviewed -> Under Review (an investigator marked it) -> Case Created (it is in a case). A flagged wallet is
    never a case until an investigator adds it to one.
    """
    wallets = list(dict.fromkeys(wallets))
    case_map = case_ids_for_wallets(session, wallets) if case_map is None else case_map
    under_review = set(session.scalars(select(WalletReview.wallet_address).where(WalletReview.wallet_address.in_(wallets), WalletReview.status == "Under Review"))) if wallets else set()
    return {w: "Case Created" if case_map.get(w) else "Under Review" if w in under_review else "Unreviewed" for w in wallets}
