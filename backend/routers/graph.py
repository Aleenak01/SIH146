from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..errors import AppError
from ..schemas import GraphOut
from ..services import graph as graph_service

router = APIRouter(prefix="/api/graph", tags=["relationship graph"])


@router.get("", response_model=GraphOut)
def get_graph(
    session: Session = Depends(get_session),
    wallet: Annotated[str | None, Query(max_length=128, description="Focus on a wallet")] = None,
    transaction: Annotated[str | None, Query(max_length=80, description="Focus on a transaction")] = None,
    cluster: Annotated[str | None, Query(max_length=64, description="Focus on a cluster")] = None,
    leads: Annotated[bool, Query(description="Overview: the current leads and their surroundings")] = False,
    depth: Annotated[int, Query(ge=0, le=3, description="Transfer hops to expand from the focus")] = 1,
    node_types: Annotated[str | None, Query(description="Comma list of: wallet, transaction, ip_observation, device, session (wallet is always included)")] = None,
    edge_types: Annotated[str | None, Query(description="Comma list of: sent_to, received_from, observed_from, associated_with, same_device, same_session")] = None,
    max_nodes: Annotated[int, Query(ge=5, le=500)] = 150,
    max_transactions: Annotated[int, Query(ge=1, le=500)] = 150,
    lead_limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """
    Nodes and edges for the investigator's relationship graph. Give exactly one focus: `wallet`, `transaction`,
    `cluster`, or `leads=true`. By default it shows wallets and the transfers between them; add node types to see
    individual transactions or the SYNTHETIC IP / device / session observations.
    """
    focuses = [(k, v) for k, v in (("wallet", wallet), ("transaction", transaction), ("cluster", cluster)) if v] + ([("leads", "leads")] if leads else [])
    if len(focuses) != 1:
        raise AppError(422, "graph_focus_required", "Give exactly one focus: wallet, transaction, cluster, or leads=true.")
    ftype, fid = focuses[0]
    req = graph_service.GraphRequest(
        focus_type=ftype, focus_id=None if ftype == "leads" else fid, depth=depth,
        node_types=graph_service.parse_csv_param(node_types, graph_service.NODE_TYPES, "node_types") or {"wallet"},
        edge_types=graph_service.parse_csv_param(edge_types, graph_service.EDGE_TYPES, "edge_types"),
        max_nodes=max_nodes, max_transactions=max_transactions, lead_limit=lead_limit)
    return graph_service.build_graph(session, req)
