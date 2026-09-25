"""Queries behind the cluster endpoints."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis.clustering import METHOD_LABELS, METHOD_NET, NOTE
from ..models import EntityCluster, EntityClusterMember, Transaction
from ..schemas import ClusterDetail, ClusterOut, ClusterRelationship, ClusterWallet
from . import graph as graph_service


def _cluster_out(c: EntityCluster) -> ClusterOut:
    return ClusterOut(
        cluster_id=c.cluster_id, method=c.method, method_label=METHOD_LABELS.get(c.method, c.method), source=c.source,
        entity_count=c.entity_count, wallet_count=c.wallet_count, transaction_count=c.transaction_count,
        network_observation_count=c.network_observation_count, priority_summary=c.priority_summary, created_at=c.created_at, updated_at=c.updated_at)


def list_clusters(session: Session, *, method, source, min_wallets, has_leads, q, sort, limit, offset):
    stmt = select(EntityCluster)
    if method:
        stmt = stmt.where(EntityCluster.method == method)
    if source:
        stmt = stmt.where(EntityCluster.source == source)
    if min_wallets:
        stmt = stmt.where(EntityCluster.wallet_count >= min_wallets)
    if q:
        members = select(EntityClusterMember.cluster_id).where(EntityClusterMember.entity_id.contains(q, autoescape=True))
        stmt = stmt.where(EntityCluster.cluster_id.contains(q, autoescape=True) | EntityCluster.cluster_id.in_(members))
    clusters = list(session.scalars(stmt))
    if has_leads is not None:
        clusters = [c for c in clusters if bool((c.priority_summary or {}).get("lead_count")) == has_leads]

    def lead_key(c: EntityCluster):
        ps = c.priority_summary or {}
        return (-(ps.get("lead_count") or 0), -(ps.get("max_combined_score") or 0.0), -c.wallet_count, c.cluster_id)

    keys = {"priority": lead_key, "size": lambda c: (-c.wallet_count, -c.transaction_count, c.cluster_id), "id": lambda c: c.cluster_id}
    clusters.sort(key=keys[sort])
    return len(clusters), [_cluster_out(c) for c in clusters[offset: offset + limit]]


def get_cluster_detail(session: Session, cluster_id: str) -> ClusterDetail | None:
    c = session.get(EntityCluster, cluster_id)
    if c is None:
        return None
    members = list(session.scalars(select(EntityClusterMember).where(EntityClusterMember.cluster_id == cluster_id)))
    wallets = sorted(m.entity_id for m in members if m.entity_type == "wallet")
    by_type = lambda t: sorted(m.entity_id for m in members if m.entity_type == t)

    info = graph_service._wallet_info(session, wallets)
    wallet_rows = [ClusterWallet(
        wallet_address=w, is_lead=bool(info[w].get("is_lead")), ml_prediction=info[w].get("ml_prediction"), priority_level=info[w].get("priority_level"),
        priority_rank=info[w].get("priority_rank"), combined_score=info[w].get("combined_score"), transaction_count=info[w].get("transaction_count", 0))
        for w in wallets]
    wallet_rows.sort(key=lambda r: (r.priority_rank is None, r.priority_rank or 0, r.wallet_address))

    pairs = session.execute(
        select(Transaction.sender_wallet, Transaction.receiver_wallet, func.count(), func.sum(Transaction.amount_btc))
        .where(Transaction.sender_wallet.in_(wallets), Transaction.receiver_wallet.in_(wallets))
        .group_by(Transaction.sender_wallet, Transaction.receiver_wallet).order_by(func.count().desc(), Transaction.sender_wallet).limit(100)).all()
    relationships = [ClusterRelationship(source=s, target=r, transfers=n, total_btc=round(btc, 8)) for s, r, n, btc in pairs]

    node_types = {"wallet", "device", "ip_observation"} if c.method == METHOD_NET else {"wallet"}
    graph = graph_service.build_graph(session, graph_service.GraphRequest(
        focus_type="cluster", focus_id=cluster_id, depth=0, node_types=node_types, max_nodes=200))
    return ClusterDetail(
        **_cluster_out(c).model_dump(), wallets=wallet_rows, devices=by_type("device"), ip_addresses=by_type("ip"), sessions=by_type("session"),
        internal_relationships=relationships, graph=graph, note=NOTE)
