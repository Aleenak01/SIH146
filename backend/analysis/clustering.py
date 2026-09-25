"""
Related-entity clusters. Two methods, stored side by side and always labelled with the method:

  shared_network_observation  wallets linked by a shared (SYNTHETIC) device or IP, joined into connected groups. These
                              link wallets that may have no transfers between them.
  transaction_community       Louvain community detection (modularity, seeded) on the wallet-to-wallet transfer graph,
                              weighted by number of transfers. Plain connected components would return one group holding
                              every wallet, because the transfer graph is a single connected component.

A cluster means "connected activity or shared synthetic infrastructure". It does NOT mean common ownership, a
criminal group, or wrongdoing, and it is never described that way. Only clusters with 2+ wallets are stored.

`transaction_count` of a cluster = transactions in which at least one member wallet is a party.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import delete, func, select

from ..database import Database
from ..models import (
    AnomalyResult, EntityCluster, EntityClusterMember, FusionResult, InvestigativeLead, NetworkObservation, Transaction, utcnow,
)
from ..services.ingest import WRITE_LOCK
from .service import latest_run

METHOD_NET = "shared_network_observation"
METHOD_TXC = "transaction_community"
METHOD_LABELS = {
    METHOD_NET: "Shared synthetic network observation (device or IP)",
    METHOD_TXC: "Transaction community (Louvain on the transfer graph)",
}
METHOD_PREFIX = {METHOD_NET: "NET", METHOD_TXC: "TXC"}
LOUVAIN_SEED = 146
LOUVAIN_RESOLUTION = 1.0
NOTE = ("Related-entity cluster: entities connected by the method above. It indicates connected activity or shared "
        "synthetic infrastructure, not common ownership, and it is not evidence of wrongdoing. Requires investigator review.")


@dataclass
class ClusterData:
    method: str
    wallets: list[str]
    devices: set[str] = field(default_factory=set)
    ips: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    _assigned_id: str | None = None

    @property
    def default_id(self) -> str:
        """Id for a brand-new cluster: method prefix + its lowest wallet address."""
        return f"{METHOD_PREFIX[self.method]}-{min(self.wallets)}"

    @property
    def cluster_id(self) -> str:
        return self._assigned_id or self.default_id


# ---- pure computation ---------------------------------------------------------------------------------------
class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def compute_network_clusters(observations: Iterable[tuple[str, str | None, str | None, str | None]]) -> list[ClusterData]:
    """observations: (wallet, ip, device, session). Wallets sharing a device, IP or session end up in one cluster."""
    uf = _UnionFind()
    holder: dict[tuple[str, str], str] = {}                   # ("device"|"ip"|"session", value) -> first wallet seen
    obs = list(observations)
    for wallet, ip, device, session in obs:
        uf.find(wallet)
        for kind, value in (("device", device), ("ip", ip), ("session", session)):
            if value:
                first = holder.setdefault((kind, value), wallet)
                uf.union(first, wallet)
    groups: dict[str, ClusterData] = {}
    for wallet, ip, device, session in obs:
        c = groups.setdefault(uf.find(wallet), ClusterData(METHOD_NET, []))
        if wallet not in c.wallets:
            c.wallets.append(wallet)
        if device:
            c.devices.add(device)
        if ip:
            c.ips.add(ip)
        if session:
            c.sessions.add(session)
    clusters = []
    for c in groups.values():
        if len(c.wallets) >= 2:
            c.wallets.sort()
            clusters.append(c)
    return sorted(clusters, key=lambda c: c.cluster_id)


def compute_transaction_communities(transfers: Iterable[tuple[str, str]]) -> list[ClusterData]:
    """transfers: (sender, receiver). Louvain communities of the undirected transfer-count-weighted wallet graph."""
    import networkx as nx

    g = nx.Graph()
    for s, r in transfers:
        if g.has_edge(s, r):
            g[s][r]["weight"] += 1
        else:
            g.add_edge(s, r, weight=1)
    if g.number_of_edges() == 0:
        return []
    communities = nx.community.louvain_communities(g, weight="weight", resolution=LOUVAIN_RESOLUTION, seed=LOUVAIN_SEED)
    clusters = [ClusterData(METHOD_TXC, sorted(c)) for c in communities if len(c) >= 2]
    return sorted(clusters, key=lambda c: c.cluster_id)


# ---- persistence ----------------------------------------------------------------------------------------------
@dataclass
class ClusterRefresh:
    network_clusters: int = 0
    transaction_communities: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        return self.network_clusters + self.transaction_communities


def _priority_summary(session, source: str, wallets: Sequence[str]) -> dict:
    run = latest_run(session, source)
    if run is None:
        return {"analysed": False}
    fusion = {f.wallet_address: f for f in session.scalars(select(FusionResult).where(FusionResult.run_id == run.run_id, FusionResult.wallet_address.in_(wallets)))}
    anomalous = set(session.scalars(select(AnomalyResult.wallet_address).where(
        AnomalyResult.run_id == run.run_id, AnomalyResult.anomaly_prediction == "Anomalous", AnomalyResult.wallet_address.in_(wallets))))
    leads = set(session.scalars(select(InvestigativeLead.wallet_address).where(InvestigativeLead.wallet_address.in_(wallets))))
    scored = [fusion[w] for w in wallets if w in fusion]
    top = min(scored, key=lambda f: f.priority_rank) if scored else None
    return {
        "analysed": True, "run_id": run.run_id, "scored_wallets": len(scored), "lead_count": len(leads), "anomalous_count": len(anomalous),
        "high_priority_count": sum(f.priority_level == "High" for f in scored),
        "max_combined_score": max((f.combined_score for f in scored), default=None),
        "mean_combined_score": round(sum(f.combined_score for f in scored) / len(scored), 6) if scored else None,
        "top_wallet": top.wallet_address if top else None, "top_priority_rank": top.priority_rank if top else None,
        "note": "Priority summary from the prototype fusion score (not statistically validated).",
    }


def assign_stable_ids(computed: list[ClusterData], existing: dict[str, tuple[str, set[str]]]) -> None:
    """
    Keep cluster ids stable as clusters grow, shrink, merge or split. `existing`: id -> (method, wallet set).
    A recomputed cluster takes the id of the existing cluster of the same method that it overlaps most (each id used
    once, largest overlaps first). Clusters with no such match get a new id from their lowest wallet, with a numeric
    suffix if that id is already taken. Cases and bookmarks that refer to a cluster therefore keep pointing at it.
    """
    pairs = []
    for i, c in enumerate(computed):
        wallets = set(c.wallets)
        for eid, (method, members) in existing.items():
            if method == c.method and (overlap := len(wallets & members)):
                # largest overlap first; on a tie prefer the cluster that still has the id's own lowest wallet
                pairs.append((-overlap, c.default_id != eid, eid, c.default_id, i))
    taken: set[str] = set()
    assigned: dict[int, str] = {}
    for _neg, _tie, eid, _default, i in sorted(pairs):
        if i not in assigned and eid not in taken:
            assigned[i] = eid
            taken.add(eid)
    reserved = set(existing) | taken
    for i, c in enumerate(computed):
        if i in assigned:
            c._assigned_id = assigned[i]
            continue
        new_id, n = c.default_id, 1
        while new_id in reserved:
            n += 1
            new_id = f"{c.default_id}-{n}"
        reserved.add(new_id)
        c._assigned_id = new_id


def refresh_clusters(db: Database, source: str = "synthetic") -> ClusterRefresh:
    """Recompute both cluster methods for one source and upsert them. Unchanged clusters keep their created_at/updated_at."""
    with db.session() as s:
        transfers = [tuple(r) for r in s.execute(select(Transaction.sender_wallet, Transaction.receiver_wallet).where(Transaction.source == source))]
        observations = [tuple(r) for r in s.execute(
            select(NetworkObservation.wallet_address, NetworkObservation.ip_address, NetworkObservation.device_id, NetworkObservation.session_id)
            .join(Transaction, Transaction.transaction_id == NetworkObservation.transaction_id).where(Transaction.source == source))]
    computed = compute_network_clusters(observations) + compute_transaction_communities(transfers)
    with db.session() as s:
        members: dict[str, set[str]] = {}
        for cid, wallet in s.execute(select(EntityClusterMember.cluster_id, EntityClusterMember.entity_id).where(EntityClusterMember.entity_type == "wallet")):
            members.setdefault(cid, set()).add(wallet)
        existing = {c.cluster_id: (c.method, members.get(c.cluster_id, set())) for c in s.scalars(select(EntityCluster).where(EntityCluster.source == source))}
    assign_stable_ids(computed, existing)

    # index: method -> wallet -> cluster id, to count transactions and observations per cluster in one pass
    index: dict[str, dict[str, str]] = {METHOD_NET: {}, METHOD_TXC: {}}
    for c in computed:
        for w in c.wallets:
            index[c.method][w] = c.cluster_id
    tx_counts: dict[str, int] = {}
    for snd, rcv in transfers:
        for method, idx in index.items():
            for cid in {idx.get(snd), idx.get(rcv)} - {None}:
                tx_counts[cid] = tx_counts.get(cid, 0) + 1
    obs_counts: dict[str, int] = {}
    for wallet, *_ in observations:
        cid = index[METHOD_NET].get(wallet)
        if cid:
            obs_counts[cid] = obs_counts.get(cid, 0) + 1

    result = ClusterRefresh(
        network_clusters=sum(c.method == METHOD_NET for c in computed), transaction_communities=sum(c.method == METHOD_TXC for c in computed))
    with WRITE_LOCK, db.transaction() as s:
        existing = {c.cluster_id: c for c in s.scalars(select(EntityCluster).where(EntityCluster.source == source))}
        keep: set[str] = set()
        for c in computed:
            cid = c.cluster_id
            keep.add(cid)
            entities = [("wallet", w) for w in c.wallets] + [("device", d) for d in sorted(c.devices)] + [("ip", i) for i in sorted(c.ips)] + \
                       [("session", x) for x in sorted(c.sessions)]
            values = dict(method=c.method, entity_count=len(entities), wallet_count=len(c.wallets), transaction_count=tx_counts.get(cid, 0),
                          network_observation_count=obs_counts.get(cid, 0), priority_summary=_priority_summary(s, source, c.wallets))
            row = existing.get(cid)
            if row is None:
                s.add(EntityCluster(cluster_id=cid, source=source, **values))
                result.created += 1
                changed_members = True
            else:
                changed = False
                for k, v in values.items():
                    if getattr(row, k) != v:
                        setattr(row, k, v)
                        changed = True
                result.updated += changed
                current = {(m.entity_type, m.entity_id) for m in s.scalars(select(EntityClusterMember).where(EntityClusterMember.cluster_id == cid))}
                changed_members = current != set(entities)
            if changed_members:
                s.execute(delete(EntityClusterMember).where(EntityClusterMember.cluster_id == cid))
                s.flush()
                s.add_all([EntityClusterMember(cluster_id=cid, entity_type=t, entity_id=e) for t, e in entities])
        for cid in set(existing) - keep:
            s.execute(delete(EntityClusterMember).where(EntityClusterMember.cluster_id == cid))
            s.execute(delete(EntityCluster).where(EntityCluster.cluster_id == cid))
            result.removed += 1
    return result
