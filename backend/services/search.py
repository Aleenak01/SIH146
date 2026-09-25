"""
Unified search across everything an investigator may look for.

Categories: wallets · transactions · clusters · ip_observations (observation IDs and IP addresses) · devices · sessions · cases.
Text is matched literally (case-insensitive substring; wildcards are escaped). Exact matches come first. Each category
reports its full match count and returns at most `limit` hits.
"""

from __future__ import annotations

from sqlalchemy import case as sql_case, func, or_, select
from sqlalchemy.orm import Session

from ..models import Case, CaseItem, EntityCluster, EntityClusterMember, InvestigativeLead, NetworkObservation, Transaction, Wallet
from ..schemas import SYNTHETIC_NETWORK_NOTE, SearchCategory, SearchHit, SearchOut
from . import case_links

CATEGORIES = ("wallets", "transactions", "clusters", "ip_observations", "devices", "sessions", "cases")
MIN_QUERY = 2


def _exact_first(column, q: str):
    return sql_case((func.lower(column) == q.lower(), 0), else_=1)


def _wallets(session: Session, q: str, limit: int) -> SearchCategory:
    where = Wallet.address.contains(q, autoescape=True)
    total = session.scalar(select(func.count()).select_from(Wallet).where(where)) or 0
    rows = list(session.scalars(select(Wallet).where(where).order_by(_exact_first(Wallet.address, q), Wallet.address).limit(limit)))
    addresses = [w.address for w in rows]
    leads = {l.wallet_address: l for l in session.scalars(select(InvestigativeLead).where(InvestigativeLead.wallet_address.in_(addresses)))} if addresses else {}
    cases = case_links.case_ids_for_wallets(session, addresses)
    hits = []
    for w in rows:
        lead = leads.get(w.address)
        sub = f"{w.source} wallet" + (f" · lead, priority #{lead.priority_rank} ({lead.priority_level})" if lead else "")
        hits.append(SearchHit(type="wallet", id=w.address, label=w.address, subtitle=sub, match="wallet address",
                              data={"source": w.source, "is_lead": lead is not None, "priority_level": lead.priority_level if lead else None,
                                    "ml_prediction": lead.ml_prediction if lead else None, "case_ids": cases.get(w.address, [])}))
    return SearchCategory(total=total, items=hits)


def _transactions(session: Session, q: str, limit: int) -> SearchCategory:
    where = Transaction.transaction_id.contains(q, autoescape=True)
    total = session.scalar(select(func.count()).select_from(Transaction).where(where)) or 0
    rows = session.scalars(select(Transaction).where(where).order_by(_exact_first(Transaction.transaction_id, q), Transaction.transaction_id).limit(limit))
    return SearchCategory(total=total, items=[SearchHit(
        type="transaction", id=t.transaction_id, label=t.transaction_id, match="transaction ID",
        subtitle=f"{t.sender_wallet} → {t.receiver_wallet} · {t.amount_btc:g} BTC · {t.timestamp:%Y-%m-%d %H:%M}",
        data={"source": t.source, "sender": t.sender_wallet, "receiver": t.receiver_wallet, "amount_btc": t.amount_btc}) for t in rows])


def _clusters(session: Session, q: str, limit: int) -> SearchCategory:
    members = select(EntityClusterMember.cluster_id).where(EntityClusterMember.entity_type == "wallet", EntityClusterMember.entity_id.contains(q, autoescape=True))
    where = or_(EntityCluster.cluster_id.contains(q, autoescape=True), EntityCluster.cluster_id.in_(members))
    total = session.scalar(select(func.count()).select_from(EntityCluster).where(where)) or 0
    rows = list(session.scalars(select(EntityCluster).where(where).order_by(_exact_first(EntityCluster.cluster_id, q), EntityCluster.cluster_id).limit(limit)))
    hits = []
    for c in rows:
        by_id = q.lower() in c.cluster_id.lower()
        hits.append(SearchHit(type="cluster", id=c.cluster_id, label=c.cluster_id, match="cluster ID" if by_id else "member wallet",
                              subtitle=f"{c.wallet_count} wallets · {c.method.replace('_', ' ')}",
                              data={"method": c.method, "wallet_count": c.wallet_count, "lead_count": (c.priority_summary or {}).get("lead_count")}))
    return SearchCategory(total=total, items=hits)


def _ip_observations(session: Session, q: str, limit: int) -> SearchCategory:
    """Observation IDs (obs-…) match individually; an IP address matches once per distinct address with its observation count."""
    N = NetworkObservation
    hits: list[SearchHit] = []
    if q.lower().startswith("obs") or "-" in q:
        where = N.observation_id.contains(q, autoescape=True)
        total = session.scalar(select(func.count()).select_from(N).where(where)) or 0
        for o in session.scalars(select(N).where(where).order_by(_exact_first(N.observation_id, q), N.observation_id).limit(limit)):
            hits.append(SearchHit(type="ip_observation", id=o.observation_id, label=o.observation_id, match="observation ID",
                                  subtitle=f"{o.ip_address} · {o.wallet_address} · {o.device_id} · synthetic",
                                  data={"ip_address": o.ip_address, "wallet_address": o.wallet_address, "transaction_id": o.transaction_id, "synthetic": True}))
        return SearchCategory(total=total, items=hits)
    where = N.ip_address.contains(q, autoescape=True)
    groups = session.execute(select(N.ip_address, func.count(), func.count(func.distinct(N.wallet_address))).where(where).group_by(N.ip_address)
                             .order_by(_exact_first(N.ip_address, q), N.ip_address).limit(limit)).all()
    total = session.scalar(select(func.count(func.distinct(N.ip_address))).where(where)) or 0
    for ip, n, wallets in groups:
        hits.append(SearchHit(type="ip", id=ip, label=ip, match="IP address", subtitle=f"{n} synthetic observation(s) · {wallets} wallet(s)",
                              data={"observation_count": n, "wallet_count": wallets, "synthetic": True}))
    return SearchCategory(total=total, items=hits)


def _grouped(session: Session, q: str, limit: int, column, kind: str, label: str) -> SearchCategory:
    N = NetworkObservation
    where = column.contains(q, autoescape=True)
    total = session.scalar(select(func.count(func.distinct(column))).where(where)) or 0
    rows = session.execute(select(column, func.count(), func.count(func.distinct(N.wallet_address))).where(where).group_by(column)
                           .order_by(_exact_first(column, q), column).limit(limit)).all()
    return SearchCategory(total=total, items=[SearchHit(type=kind, id=v, label=v, match=label, subtitle=f"{n} synthetic observation(s) · {w} wallet(s)",
                                                        data={"observation_count": n, "wallet_count": w, "synthetic": True}) for v, n, w in rows])


def _cases(session: Session, q: str, limit: int) -> SearchCategory:
    in_items = select(CaseItem.case_id).where(CaseItem.item_id.contains(q, autoescape=True))
    where = or_(Case.case_id.contains(q, autoescape=True), Case.title.contains(q, autoescape=True), Case.case_id.in_(in_items))
    total = session.scalar(select(func.count()).select_from(Case).where(where)) or 0
    rows = list(session.scalars(select(Case).where(where).order_by(_exact_first(Case.case_id, q), Case.case_id).limit(limit)))
    return SearchCategory(total=total, items=[SearchHit(
        type="case", id=c.case_id, label=c.case_id, match="case ID" if q.lower() in c.case_id.lower() else "title or item", subtitle=f"{c.title} · {c.status}",
        data={"status": c.status, "priority": c.priority}) for c in rows])


def search(session: Session, q: str, types: set[str] | None = None, limit: int = 10) -> SearchOut:
    q = q.strip()
    wanted = types or set(CATEGORIES)
    N = NetworkObservation
    runners = {
        "wallets": lambda: _wallets(session, q, limit),
        "transactions": lambda: _transactions(session, q, limit),
        "clusters": lambda: _clusters(session, q, limit),
        "ip_observations": lambda: _ip_observations(session, q, limit),
        "devices": lambda: _grouped(session, q, limit, N.device_id, "device", "device ID"),
        "sessions": lambda: _grouped(session, q, limit, N.session_id, "session", "session ID"),
        "cases": lambda: _cases(session, q, limit),
    }
    categories = {name: runners[name]() for name in CATEGORIES if name in wanted}
    return SearchOut(query=q, total=sum(c.total for c in categories.values()), categories=categories,
                     notes=[SYNTHETIC_NETWORK_NOTE] if any(k in categories for k in ("ip_observations", "devices", "sessions")) else [])
