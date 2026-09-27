"""Address entity endpoints (Phase 2): common-input-ownership entities, network correlation, and their graph. SYNTHETIC data only."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..analysis.correlation import refresh_correlation
from ..analysis.entities import refresh_entities
from ..database import Database
from ..deps import get_db, get_session
from ..errors import AppError
from ..services import entity_queries
from ..services.entity_graph import EDGE_TYPES, NODE_TYPES, EntityGraphRequest, build_entity_graph

router = APIRouter(tags=["address entities (synthetic, phase 2)"])


@router.get("/api/entities")
def list_entities(
    session: Session = Depends(get_session),
    source: Literal["synthetic", "real_bitcoin"] | None = "synthetic",
    min_addresses: Annotated[int | None, Query(ge=1, le=10_000)] = None,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    asn: Annotated[int | None, Query(ge=0)] = None,
    ip: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=128, description="Entity id or a member address")] = None,
    sort: Literal["size", "activity", "id"] = "size",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """
    Address entities from the common-input-ownership heuristic: addresses spent together as inputs of one
    transaction. An entity indicates likely common control, never proof. Built entirely from the rich (address-level)
    SYNTHETIC data (Phase 1); distinct from the wallet-level clusters at /api/clusters.
    """
    total, items = entity_queries.list_entities(session, source=source, min_addresses=min_addresses, country=country, asn=asn, ip=ip, q=q, sort=sort, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.post("/api/entities/run")
def run_entities(source: Literal["synthetic", "real_bitcoin"] = "synthetic", db: Database = Depends(get_db)) -> dict[str, Any]:
    """
    (Re)build address entities and network correlation now from the rich data already imported for this source.
    Additive, idempotent and deterministic. NOT run by the monitor or the wallet-level analysis run in this phase.
    """
    er = refresh_entities(db, source)
    if not er.rich_data_available:
        return {"rich_data_available": False, "entities": 0,
                "message": f"No rich (address-level) data for source={source!r}. Import rich records first (see POST /api/import/rich or `backend.cli import-rich`)."}
    cr = refresh_correlation(db, source)
    return {
        "rich_data_available": True, "entities": er.entities, "created": er.created, "updated": er.updated, "removed": er.removed,
        "addresses_total": er.addresses_total, "entity_ip_links": cr.entity_ip_links, "entity_links": cr.entity_links, "findings": cr.findings,
    }


@router.get("/api/entities/{entity_id}")
def get_entity(entity_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """One entity: its addresses, the transactions it spent from, IP/ASN/country links, related entities and correlation findings."""
    detail = entity_queries.get_entity_detail(session, entity_id)
    if detail is None:
        raise AppError(404, "not_found", f"No entity {entity_id!r}.")
    return detail


@router.get("/api/entity-graph")
def entity_graph(
    session: Session = Depends(get_session),
    focus: Literal["entity", "address", "ip", "txid"] = "entity",
    id: Annotated[str, Query(alias="id")] = "",
    depth: Annotated[int, Query(ge=0, le=3)] = 1,
    node_types: Annotated[str | None, Query(description=f"comma-separated: {', '.join(NODE_TYPES)}")] = None,
    edge_types: Annotated[str | None, Query(description=f"comma-separated: {', '.join(EDGE_TYPES)}")] = None,
    max_nodes: Annotated[int, Query(ge=1, le=500)] = 150,
) -> dict[str, Any]:
    """
    Entity / address / transaction / IP / ASN / country graph, built from the rich SYNTHETIC address-level data.
    This is a separate graph from the wallet-level /api/graph.
    """
    if not id:
        raise AppError(422, "validation_error", "id is required.")
    nt = set(node_types.split(",")) if node_types else {"entity", "address", "transaction", "ip"}
    if bad_nt := (nt - set(NODE_TYPES)):
        raise AppError(422, "validation_error", f"Unknown node_types: {', '.join(sorted(bad_nt))}. Allowed: {', '.join(NODE_TYPES)}.")
    et = set(edge_types.split(",")) if edge_types else None
    if et is not None and (bad_et := (et - set(EDGE_TYPES))):
        raise AppError(422, "validation_error", f"Unknown edge_types: {', '.join(sorted(bad_et))}. Allowed: {', '.join(EDGE_TYPES)}.")
    req = EntityGraphRequest(focus_type=focus, focus_id=id, depth=depth, node_types=nt, edge_types=et, max_nodes=max_nodes)
    return build_entity_graph(session, req)
