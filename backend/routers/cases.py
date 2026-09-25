from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import Database
from ..deps import get_db, get_session
from ..schemas import (
    CaseCreate, CaseDetail, CaseHistoryOut, CaseItemIn, CaseItemOut, CasePage, CaseUpdate, GraphOut, NoteIn, ReviewedIn, TransactionOut, TransactionPage,
)
from ..services import cases as svc
from ..services import graph as graph_service

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _detail(db: Database, case_id: str) -> CaseDetail:
    with db.session() as s:
        return svc.detail(s, svc.get_case_or_404(s, case_id))


@router.post("", response_model=CaseDetail, status_code=201)
def create_case(payload: CaseCreate, db: Database = Depends(get_db)):
    """
    Open a case. Only an investigator does this: a lead, an anomaly or a cluster never becomes a case by itself.
    Items (lead, wallet, transaction, cluster) may be included; each is stored with the evidence as it is now.
    """
    with db.transaction() as s:
        case_id = svc.create_case(s, payload).case_id
    return _detail(db, case_id)


@router.get("", response_model=CasePage)
def list_cases(
    session: Session = Depends(get_session),
    status: Literal["Open", "Under investigation", "Closed"] | None = None,
    priority: Literal["High", "Medium", "Low"] | None = None,
    q: Annotated[str | None, Query(max_length=128, description="Case ID, title, description or an item ID")] = None,
    item_type: Literal["lead", "wallet", "transaction", "cluster"] | None = None,
    item_id: Annotated[str | None, Query(max_length=128, description="Only cases that contain this item")] = None,
    sort: Literal["updated_at", "created_at", "case_id"] = "updated_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    total, items = svc.list_cases(session, status=status, priority=priority, q=q, item_type=item_type, item_id=item_id, sort=sort, order=order, limit=limit, offset=offset)
    return CasePage(total=total, limit=limit, offset=offset, items=items)


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, session: Session = Depends(get_session)):
    """The whole case: items with their saved evidence and today's values, history, notes and related transactions."""
    return svc.detail(session, svc.get_case_or_404(session, case_id))


@router.patch("/{case_id}", response_model=CaseDetail)
def update_case(case_id: str, payload: CaseUpdate, db: Database = Depends(get_db)):
    """Change title, description, status, priority or assignee. Every change is recorded in the case history."""
    with db.transaction() as s:
        svc.update_case(svc.get_case_or_404(s, case_id), payload)
    return _detail(db, case_id)


@router.post("/{case_id}/items", response_model=CaseItemOut, status_code=201)
def add_item(case_id: str, payload: CaseItemIn, db: Database = Depends(get_db)):
    """Add a lead, wallet, transaction or cluster to the case. Its current evidence is saved with it."""
    with db.transaction() as s:
        case = svc.get_case_or_404(s, case_id)
        row = svc.add_item(s, case, payload)
        s.flush()
        out = CaseItemOut(item_type=row.item_type, item_id=row.item_id, added_at=row.added_at, evidence_snapshot=row.evidence_snapshot, current=svc._current(s, row.item_type, row.item_id))
    return out


@router.delete("/{case_id}/items/{item_type}/{item_id}", status_code=204)
def remove_item(case_id: str, item_type: Literal["lead", "wallet", "transaction", "cluster"], item_id: str, db: Database = Depends(get_db)):
    with db.transaction() as s:
        svc.remove_item(s, svc.get_case_or_404(s, case_id), item_type, item_id)


@router.post("/{case_id}/notes", response_model=CaseHistoryOut, status_code=201)
def add_note(case_id: str, payload: NoteIn, db: Database = Depends(get_db)):
    with db.transaction() as s:
        case = svc.get_case_or_404(s, case_id)
        svc.add_note(case, payload.text)
        s.flush()
        h = case.history[-1]
        return CaseHistoryOut(entry_id=h.entry_id, at=h.at, actor=h.actor, action=h.action, detail=h.detail)


@router.post("/{case_id}/reviews", response_model=CaseHistoryOut, status_code=201)
def mark_evidence_reviewed(case_id: str, payload: ReviewedIn, db: Database = Depends(get_db)):
    """Record that the investigator reviewed the evidence of one item of the case."""
    with db.transaction() as s:
        case = svc.get_case_or_404(s, case_id)
        svc.add_review(case, payload.item_type, payload.item_id, payload.note)
        s.flush()
        h = case.history[-1]
        return CaseHistoryOut(entry_id=h.entry_id, at=h.at, actor=h.actor, action=h.action, detail=h.detail)


@router.get("/{case_id}/history", response_model=list[CaseHistoryOut])
def history(case_id: str, session: Session = Depends(get_session)):
    case = svc.get_case_or_404(session, case_id)
    return [CaseHistoryOut(entry_id=h.entry_id, at=h.at, actor=h.actor, action=h.action, detail=h.detail) for h in case.history]


@router.get("/{case_id}/transactions", response_model=TransactionPage)
def case_transactions(
    case_id: str,
    session: Session = Depends(get_session),
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Transactions of the case's wallets plus any transactions added directly (live from the database)."""
    case = svc.get_case_or_404(session, case_id)
    total, rows, _ = svc.related_transactions(session, case, limit=limit, offset=offset, order=order)
    return TransactionPage(total=total, limit=limit, offset=offset, items=[TransactionOut.model_validate(t) for t in rows])


@router.get("/{case_id}/graph", response_model=GraphOut)
def case_graph(
    case_id: str,
    session: Session = Depends(get_session),
    depth: Annotated[int, Query(ge=0, le=3)] = 1,
    node_types: str | None = None,
    edge_types: str | None = None,
    max_nodes: Annotated[int, Query(ge=5, le=500)] = 150,
):
    """Relationship graph of the case: its wallets, cluster members and transaction parties, with their surroundings."""
    case = svc.get_case_or_404(session, case_id)
    req = graph_service.GraphRequest(
        focus_type="wallets", focus_id=case_id, depth=depth, seeds=svc.graph_seed_wallets(session, case),
        node_types=graph_service.parse_csv_param(node_types, graph_service.NODE_TYPES, "node_types") or {"wallet"},
        edge_types=graph_service.parse_csv_param(edge_types, graph_service.EDGE_TYPES, "edge_types"), max_nodes=max_nodes)
    return graph_service.build_graph(session, req)
