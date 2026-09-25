from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..analysis.clustering import refresh_clusters
from ..database import Database
from ..deps import get_db, get_session
from ..errors import AppError
from ..schemas import ClusterDetail, ClusterPage
from ..services import cluster_queries

router = APIRouter(prefix="/api/clusters", tags=["related entity clusters"])


@router.get("", response_model=ClusterPage)
def list_clusters(
    session: Session = Depends(get_session),
    method: Literal["shared_network_observation", "transaction_community"] | None = None,
    source: Literal["synthetic", "real_bitcoin"] | None = None,
    min_wallets: Annotated[int | None, Query(ge=2, le=1000)] = None,
    has_leads: bool | None = None,
    q: Annotated[str | None, Query(max_length=128, description="Cluster ID or a member wallet")] = None,
    sort: Literal["priority", "size", "id"] = "priority",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """
    Related-entity clusters: wallets connected by shared (synthetic) network observations, or by dense transfer activity
    (Louvain communities). A cluster indicates connected activity, not common ownership or wrongdoing.
    """
    total, items = cluster_queries.list_clusters(session, method=method, source=source, min_wallets=min_wallets, has_leads=has_leads, q=q, sort=sort, limit=limit, offset=offset)
    return ClusterPage(total=total, limit=limit, offset=offset, items=items)


@router.post("/refresh")
def refresh(db: Database = Depends(get_db)):
    """Recompute the clusters now (this also happens after every analysis run)."""
    r = refresh_clusters(db, "synthetic")
    return {"network_clusters": r.network_clusters, "transaction_communities": r.transaction_communities, "created": r.created, "updated": r.updated, "removed": r.removed}


@router.get("/{cluster_id}", response_model=ClusterDetail)
def get_cluster(cluster_id: str, session: Session = Depends(get_session)):
    """Members, their analysis, internal relationships and a ready-to-render graph."""
    detail = cluster_queries.get_cluster_detail(session, cluster_id)
    if detail is None:
        raise AppError(404, "not_found", f"No cluster {cluster_id!r}.")
    return detail
