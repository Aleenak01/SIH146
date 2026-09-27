"""
Case management. A case exists ONLY because an investigator created it; analysis never opens cases.

A case holds items (a lead, a wallet, a transaction or a cluster). When an item is added, the evidence as it stood at
that moment is stored with it (`evidence_snapshot`), and the case detail also shows today's values, so anything that
changed since (a re-analysis, new transactions) is visible without rewriting what the investigator originally saw.
Every change is recorded in the case history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import (
    AddressEntity, Case, CaseHistory, CaseItem, EntityCluster, EntityClusterMember, NetworkObservation, Transaction, utcnow,
)
from ..schemas import (
    CaseCreate, CaseDetail, CaseHistoryOut, CaseItemIn, CaseItemOut, CaseSummary, CaseUpdate, ObservationOut,
)
from . import cluster_queries, entity_queries, leads as leads_service, queries
from .ingest import WRITE_LOCK

ACTOR = "Investigator"          # no authentication in the local prototype
ADDED_ACTION = {"lead": "lead_added", "wallet": "wallet_added", "transaction": "transaction_added", "cluster": "cluster_added", "entity": "entity_added"}
CASE_PREFIX = "CASE-"


# ---- helpers ----------------------------------------------------------------------------------------------------
def _log(case: Case, action: str, detail: str | None = None) -> None:
    now = utcnow()
    case.history.append(CaseHistory(at=now, actor=ACTOR, action=action, detail=detail))
    case.updated_at = now


def _next_case_id(session: Session) -> str:
    n = session.scalar(select(func.max(cast(func.substr(Case.case_id, len(CASE_PREFIX) + 1), Integer))).where(Case.case_id.like(f"{CASE_PREFIX}%")))
    return f"{CASE_PREFIX}{(n or 0) + 1:04d}"


def get_case_or_404(session: Session, case_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise AppError(404, "not_found", f"No case {case_id!r}.")
    return case


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# ---- evidence snapshots -----------------------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    """entity_queries returns plain dicts (no Pydantic model backs them), so datetimes need converting by hand
    before they go into the JSON evidence_snapshot column -- same convention as the isoformat() calls above."""
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def build_snapshot(session: Session, item_type: str, item_id: str) -> dict[str, Any]:
    """The evidence for an item as it stands now (JSON-safe). Raises 404 if the item does not exist."""
    taken = utcnow().isoformat() + "Z"
    if item_type == "wallet":
        stats = queries.get_wallet(session, item_id)
        if stats is None:
            raise AppError(404, "not_found", f"No wallet {item_id!r}.")
        return {"kind": "wallet", "taken_at": taken, "wallet": stats.model_dump(mode="json"),
                "analysis": leads_service.wallet_analysis(session, item_id).model_dump(mode="json")}
    if item_type == "lead":
        detail = leads_service.get_lead_detail(session, item_id)
        if detail is None:
            raise AppError(404, "not_found", f"{item_id!r} is not a current investigative lead. Add it as a wallet instead.")
        data = detail.model_dump(mode="json")
        data["related_transactions"] = {"total": data["related_transactions"]["total"]}      # the transactions themselves stay live
        return {"kind": "lead", "taken_at": taken, **data}
    if item_type == "transaction":
        tx = session.get(Transaction, item_id)
        if tx is None:
            raise AppError(404, "not_found", f"No transaction {item_id!r}.")
        obs = session.scalars(select(NetworkObservation).where(NetworkObservation.transaction_id == item_id).order_by(NetworkObservation.observation_id))
        return {
            "kind": "transaction", "taken_at": taken,
            "transaction": {"transaction_id": tx.transaction_id, "timestamp": tx.timestamp.isoformat() + "Z", "sender_wallet": tx.sender_wallet,
                            "receiver_wallet": tx.receiver_wallet, "amount_btc": tx.amount_btc, "input_count": tx.input_count,
                            "output_count": tx.output_count, "source": tx.source, "source_ref": tx.source_ref},
            "synthetic_network_observations": [ObservationOut.model_validate(o).model_dump(mode="json") for o in obs],
        }
    if item_type == "cluster":
        d = cluster_queries.get_cluster_detail(session, item_id)
        if d is None:
            raise AppError(404, "not_found", f"No cluster {item_id!r}.")
        data = d.model_dump(mode="json", exclude={"graph", "internal_relationships", "sessions"})
        data["session_count"] = len(d.sessions)
        return {"kind": "cluster", "taken_at": taken, **data}
    if item_type == "entity":
        data = entity_queries.get_entity_detail(session, item_id)
        if data is None:
            raise AppError(404, "not_found", f"No address entity {item_id!r}.")
        data = {**data, "addresses": data["addresses"][:200]}       # an entity can have many addresses; cap the stored snapshot
        return _json_safe({"kind": "entity", "taken_at": taken, **data})
    raise AppError(422, "validation_error", f"Unknown item type {item_type!r}.")


def _current(session: Session, item_type: str, item_id: str) -> dict[str, Any] | None:
    """Today's key values for an item, shown next to its snapshot."""
    if item_type in ("wallet", "lead"):
        a = leads_service.wallet_analysis(session, item_id)
        if a is None:
            return {"exists": False}
        return {"exists": True, "scored": a.scored, "ml_prediction": a.ml_prediction, "priority_rank": a.priority_rank, "priority_level": a.priority_level,
                "combined_score": a.combined_score, "forensic_rule_count": a.forensic_rule_count, "is_lead": a.is_lead, "review_status": a.review_status}
    if item_type == "cluster":
        c = session.get(EntityCluster, item_id)
        return {"exists": False} if c is None else {"exists": True, "wallet_count": c.wallet_count, "priority_summary": c.priority_summary}
    if item_type == "entity":
        e = session.get(AddressEntity, item_id)
        return {"exists": False} if e is None else {"exists": True, "address_count": e.address_count, "transaction_count": e.transaction_count,
                                                     "total_sent_btc": e.total_sent_btc, "total_received_btc": e.total_received_btc}
    return {"exists": session.get(Transaction, item_id) is not None}


# ---- create / change ----------------------------------------------------------------------------------------------
def add_item(session: Session, case: Case, item: CaseItemIn) -> CaseItem:
    if any(i.item_type == item.type and i.item_id == item.id for i in case.items):
        raise AppError(409, "duplicate_item", f"{item.type} {item.id!r} is already in {case.case_id}.")
    snapshot = build_snapshot(session, item.type, item.id)
    row = CaseItem(item_type=item.type, item_id=item.id, evidence_snapshot=snapshot)
    case.items.append(row)
    detail = f"{item.type.capitalize()} {item.id} added to the case"
    if item.note:
        detail += f". Note: {item.note}"
    _log(case, ADDED_ACTION[item.type], detail)
    return row


def create_case(session: Session, payload: CaseCreate) -> Case:
    keys = [(i.type, i.id) for i in payload.items]
    if len(keys) != len(set(keys)):
        raise AppError(422, "validation_error", "The same item is listed more than once.")
    with WRITE_LOCK:
        case = Case(case_id=_next_case_id(session), title=payload.title, description=_clean(payload.description), status="Open",
                    priority=payload.priority, assigned_to=_clean(payload.assigned_to))
        session.add(case)
        _log(case, "case_created", f"Case created by an investigator{f' with {len(payload.items)} item(s)' if payload.items else ''}")
        for item in payload.items:
            add_item(session, case, item)
        if payload.note:
            _log(case, "note_added", payload.note)
        session.flush()
    return case


def remove_item(session: Session, case: Case, item_type: str, item_id: str) -> None:
    row = next((i for i in case.items if i.item_type == item_type and i.item_id == item_id), None)
    if row is None:
        raise AppError(404, "not_found", f"{item_type} {item_id!r} is not in {case.case_id}.")
    case.items.remove(row)
    _log(case, "item_removed", f"{item_type.capitalize()} {item_id} removed from the case")


def update_case(case: Case, payload: CaseUpdate) -> None:
    labels = {"title": "title_changed", "description": "description_changed", "status": "status_changed", "priority": "priority_changed", "assigned_to": "assignment_changed"}
    # Fixed order (not set order), so the history of a multi-field change is always written the same way.
    for field in (f for f in labels if f in payload.model_fields_set):
        new = getattr(payload, field)
        new = _clean(new) if field in ("description", "assigned_to") else new
        old = getattr(case, field)
        if old == new:
            continue
        setattr(case, field, new)
        if field == "description":
            detail = "Description updated"
        else:
            detail = f"{old if old is not None else 'none'} → {new if new is not None else 'none'}"
        _log(case, labels[field], detail)


def add_note(case: Case, text: str) -> None:
    _log(case, "note_added", text)


def add_review(case: Case, item_type: str, item_id: str, note: str | None) -> None:
    if not any(i.item_type == item_type and i.item_id == item_id for i in case.items):
        raise AppError(404, "not_found", f"{item_type} {item_id!r} is not in {case.case_id}.")
    _log(case, "evidence_reviewed", f"Evidence reviewed for {item_type} {item_id}" + (f". {note}" if note else ""))


# ---- reading ----------------------------------------------------------------------------------------------------------
def _counts(items) -> dict[str, int]:
    out = {"lead": 0, "wallet": 0, "transaction": 0, "cluster": 0, "entity": 0}
    for i in items:
        out[i.item_type] = out.get(i.item_type, 0) + 1
    return out


def summary(case: Case) -> CaseSummary:
    return CaseSummary(case_id=case.case_id, title=case.title, status=case.status, priority=case.priority, assigned_to=case.assigned_to,
                       created_at=case.created_at, updated_at=case.updated_at, item_counts=_counts(case.items))


def case_wallets(case: Case) -> list[str]:
    return sorted({i.item_id for i in case.items if i.item_type in ("lead", "wallet")})


def _transaction_clause(case: Case):
    wallets = case_wallets(case)
    tx_ids = [i.item_id for i in case.items if i.item_type == "transaction"]
    parts = []
    if wallets:
        parts += [Transaction.sender_wallet.in_(wallets), Transaction.receiver_wallet.in_(wallets)]
    if tx_ids:
        parts.append(Transaction.transaction_id.in_(tx_ids))
    return or_(*parts) if parts else None


def related_transactions(session: Session, case: Case, *, limit: int | None = None, offset: int = 0, order: str = "desc"):
    """Transactions of the case's wallets plus the transactions added explicitly (live from the database)."""
    clause = _transaction_clause(case)
    if clause is None:
        return 0, [], {"value_moved_btc": 0.0, "first_at": None, "last_at": None}
    total, btc, first, last = session.execute(
        select(func.count(), func.coalesce(func.sum(Transaction.amount_btc), 0.0), func.min(Transaction.timestamp), func.max(Transaction.timestamp)).where(clause)).one()
    rows = []
    if limit:
        col = Transaction.timestamp.desc() if order == "desc" else Transaction.timestamp.asc()
        tie = Transaction.transaction_id.desc() if order == "desc" else Transaction.transaction_id.asc()
        rows = list(session.scalars(select(Transaction).where(clause).order_by(col, tie).limit(limit).offset(offset)))
    return total, rows, {"value_moved_btc": round(btc, 8), "first_at": first, "last_at": last}


def detail(session: Session, case: Case) -> CaseDetail:
    history = [CaseHistoryOut(entry_id=h.entry_id, at=h.at, actor=h.actor, action=h.action, detail=h.detail) for h in case.history]
    total, _, stats = related_transactions(session, case)
    return CaseDetail(
        **summary(case).model_dump(), description=case.description,
        items=[CaseItemOut(item_type=i.item_type, item_id=i.item_id, added_at=i.added_at, evidence_snapshot=i.evidence_snapshot,
                           current=_current(session, i.item_type, i.item_id)) for i in sorted(case.items, key=lambda i: (i.added_at, i.item_pk))],
        history=history, notes=[h for h in history if h.action == "note_added"], wallets=case_wallets(case),
        related_transactions={"total": total, "value_moved_btc": stats["value_moved_btc"],
                              "first_at": stats["first_at"].isoformat() + "Z" if stats["first_at"] else None,
                              "last_at": stats["last_at"].isoformat() + "Z" if stats["last_at"] else None})


def list_cases(session: Session, *, status, priority, q, item_type, item_id, sort, order, limit, offset):
    stmt = select(Case)
    if status:
        stmt = stmt.where(Case.status == status)
    if priority:
        stmt = stmt.where(Case.priority == priority)
    if q:
        in_items = select(CaseItem.case_id).where(CaseItem.item_id.contains(q, autoescape=True))
        stmt = stmt.where(or_(Case.case_id.contains(q, autoescape=True), Case.title.contains(q, autoescape=True),
                              Case.description.contains(q, autoescape=True), Case.case_id.in_(in_items)))
    if item_id:
        stmt = stmt.where(Case.case_id.in_(select(CaseItem.case_id).where(CaseItem.item_id == item_id, *([CaseItem.item_type == item_type] if item_type else []))))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = {"updated_at": Case.updated_at, "created_at": Case.created_at, "case_id": Case.case_id}[sort]
    rows = list(session.scalars(stmt.order_by(col.desc() if order == "desc" else col.asc(), Case.case_id).limit(limit).offset(offset)))
    return total, [summary(c) for c in rows]


def graph_seed_wallets(session: Session, case: Case) -> list[str]:
    """Wallets to draw for a case: its wallets/leads, the members of its clusters, and the parties of its transactions."""
    seeds = list(case_wallets(case))
    for i in case.items:
        if i.item_type == "cluster":
            seeds += sorted(session.scalars(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == i.item_id, EntityClusterMember.entity_type == "wallet")))
        elif i.item_type == "transaction":
            tx = session.get(Transaction, i.item_id)
            if tx is not None:
                seeds += [tx.sender_wallet, tx.receiver_wallet]
        elif i.item_type == "entity":
            # An address entity has no owning wallet of its own; seed the graph with the wallets that sent the
            # transactions it spent from (the same read-only cross-reference GET /api/entities exposes as
            # linked_wallets), purely so the case graph has something wallet-level to draw for it.
            seeds += entity_queries.linked_wallets_for_entities(session, [i.item_id]).get(i.item_id, [])
    return list(dict.fromkeys(seeds))

