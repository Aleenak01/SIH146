"""Related-entity clusters: both methods, persistence, priority summaries, API, wording."""

from __future__ import annotations

import random

import pytest
from sqlalchemy import func, select

from backend.analysis import clustering as cl
from backend.ingestion import synthetic_network as sn_module
from backend.analysis.service import run_analysis
from backend.models import EntityCluster, EntityClusterMember, InvestigativeLead, NetworkObservation, Transaction


# ---- pure computation ---------------------------------------------------------------------------------------
def test_wallets_sharing_a_device_or_ip_form_one_cluster():
    obs = [
        ("a", "192.0.2.1", "dev-1", "s1"), ("b", "192.0.2.2", "dev-1", "s2"),          # a-b share dev-1
        ("c", "192.0.2.2", "dev-2", "s3"),                                              # c shares an IP with b -> joins a-b
        ("d", "192.0.2.9", "dev-3", "s4"),                                              # alone
        ("e", "192.0.2.7", "dev-4", "s5"), ("f", "192.0.2.8", "dev-5", "s5"),          # e-f share a session only
    ]
    clusters = cl.compute_network_clusters(obs)
    assert [(c.cluster_id, c.wallets) for c in clusters] == [("NET-a", ["a", "b", "c"]), ("NET-e", ["e", "f"])]
    a = clusters[0]
    assert a.devices == {"dev-1", "dev-2"} and a.ips == {"192.0.2.1", "192.0.2.2"} and a.sessions == {"s1", "s2", "s3"}


def test_network_clusters_match_an_independent_union_find():
    rng = random.Random(3)
    obs = [(f"w{rng.randrange(60)}", f"192.0.2.{rng.randrange(1, 90)}", f"dev-{rng.randrange(70)}", f"s{i}") for i in range(200)]
    expected = {}
    keys = {}
    for w, ip, dev, s in obs:                                                         # brute force: repeat merging until stable
        expected.setdefault(w, {w})
    changed = True
    groups = {w: {w} for w in expected}
    by_key = {}
    for w, ip, dev, s in obs:
        for k in (("ip", ip), ("dev", dev)):
            by_key.setdefault(k, set()).add(w)
    for members in by_key.values():
        merged = set(members)
        for w in members:
            merged |= groups[w]
        for w in merged:
            groups[w] = merged
    while changed:
        changed = False
        for members in by_key.values():
            union = set().union(*(groups[w] for w in members))
            for w in members:
                if groups[w] != union:
                    groups[w] = union
                    changed = True
    truth = sorted({frozenset(g) for g in groups.values() if len(g) > 1}, key=lambda g: min(g))
    got = [frozenset(c.wallets) for c in cl.compute_network_clusters(obs)]
    assert got == truth


def test_communities_split_two_dense_groups_joined_by_one_weak_link_and_are_deterministic():
    transfers = []
    for group in (["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3", "b4", "b5"]):
        for i, x in enumerate(group):
            for y in group[i + 1:]:
                transfers += [(x, y), (y, x), (x, y)]
    transfers.append(("a1", "b1"))                                                    # a single weak bridge
    first = cl.compute_transaction_communities(transfers)
    assert [c.wallets for c in first] == [["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3", "b4", "b5"]]
    assert [c.cluster_id for c in first] == ["TXC-a1", "TXC-b1"]
    assert [c.wallets for c in cl.compute_transaction_communities(transfers)] == [c.wallets for c in first]      # repeatable
    assert cl.compute_transaction_communities([]) == []


def test_transfer_graph_alone_is_one_component_which_is_why_communities_are_used():
    import networkx as nx

    rng = random.Random(1)
    edges = [(f"w{i}", f"w{rng.randrange(60)}") for i in range(60)] + [(f"w{i}", f"w{i + 1}") for i in range(59)]
    g = nx.Graph([e for e in edges if e[0] != e[1]])
    assert nx.number_connected_components(g) == 1
    assert len(cl.compute_transaction_communities(edges)) > 1


# ---- persistence --------------------------------------------------------------------------------------------------------
def test_refresh_stores_consistent_counts(networked_analysed_db):
    db = networked_analysed_db
    with db.session() as s:
        clusters = list(s.scalars(select(EntityCluster)))
        assert {c.method for c in clusters} == {cl.METHOD_NET, cl.METHOD_TXC}
        assert clusters and all(c.source == "synthetic" and c.wallet_count >= 2 for c in clusters)
        for c in clusters:
            members = list(s.scalars(select(EntityClusterMember).where(EntityClusterMember.cluster_id == c.cluster_id)))
            wallets = {m.entity_id for m in members if m.entity_type == "wallet"}
            assert (c.entity_count, c.wallet_count) == (len(members), len(wallets))
            assert c.cluster_id == f"{cl.METHOD_PREFIX[c.method]}-{min(wallets)}"
            in_tx = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.sender_wallet.in_(wallets) | Transaction.receiver_wallet.in_(wallets)))
            assert c.transaction_count == in_tx
            n_obs = s.scalar(select(func.count()).select_from(NetworkObservation).where(NetworkObservation.wallet_address.in_(wallets))) if c.method == cl.METHOD_NET else 0
            assert c.network_observation_count == n_obs
            if c.method == cl.METHOD_NET:                                                # devices/ips/sessions are members too
                kinds = {m.entity_type for m in members}
                assert {"wallet", "device", "ip"} <= kinds
        txc_wallets = [m for c in clusters if c.method == cl.METHOD_TXC for m in s.scalars(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == c.cluster_id))]
        assert len(txc_wallets) == len(set(txc_wallets))                                 # a wallet is in at most one community


def test_priority_summary_agrees_with_the_leads(networked_analysed_db):
    db = networked_analysed_db
    with db.session() as s:
        leads = set(s.scalars(select(InvestigativeLead.wallet_address)))
        for c in s.scalars(select(EntityCluster)):
            members = set(s.scalars(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == c.cluster_id, EntityClusterMember.entity_type == "wallet")))
            ps = c.priority_summary
            assert ps["analysed"] is True and ps["lead_count"] == len(members & leads)
            assert "not statistically validated" in ps["note"]
            if ps["top_wallet"]:
                assert ps["top_wallet"] in members
    with db.session() as s:
        assert any(c.priority_summary["lead_count"] for c in s.scalars(select(EntityCluster)))


def test_clusters_exist_before_any_analysis_with_an_unanalysed_summary(networked_db):
    r = cl.refresh_clusters(networked_db)
    assert r.total > 0 and r.created == r.total
    with networked_db.session() as s:
        assert all(c.priority_summary == {"analysed": False} for c in s.scalars(select(EntityCluster)))


def test_refresh_is_idempotent_and_preserves_timestamps(networked_analysed_db):
    db = networked_analysed_db
    with db.session() as s:
        before = {c.cluster_id: (c.created_at, c.updated_at) for c in s.scalars(select(EntityCluster))}
    r = cl.refresh_clusters(db)
    assert (r.created, r.updated, r.removed) == (0, 0, 0)
    with db.session() as s:
        assert {c.cluster_id: (c.created_at, c.updated_at) for c in s.scalars(select(EntityCluster))} == before


def test_ids_stay_stable_as_a_cluster_grows_even_when_a_lower_address_joins(networked_analysed_db):
    db = networked_analysed_db
    with db.session() as s:
        nets = list(s.scalars(select(EntityCluster).where(EntityCluster.method == cl.METHOD_NET)))
        members = {c.cluster_id: sorted(s.scalars(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == c.cluster_id, EntityClusterMember.entity_type == "wallet"))) for c in nets}
        clustered = {w for ws in members.values() for w in ws}
        # the cluster whose lowest wallet is highest, and the lowest wallet that is in no network cluster
        cid = max(members, key=lambda k: min(members[k]))
        lowest_outsider = min(w for w in s.scalars(select(Transaction.sender_wallet).distinct()) if w not in clustered)
        assert lowest_outsider < min(members[cid])                                     # joining it would change a min-wallet based id
        before = s.get(EntityCluster, cid)
        created, updated, count = before.created_at, before.updated_at, before.wallet_count
        device = s.scalar(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == cid, EntityClusterMember.entity_type == "device"))
        tx = s.scalars(select(Transaction).where(Transaction.sender_wallet == lowest_outsider).limit(1)).one()
        tx_id = tx.transaction_id
    with db.session() as s:
        used_ips = set(s.scalars(select(NetworkObservation.ip_address).distinct()))
    free_ip = next(ip for ip in sn_module.IP_POOL if ip not in used_ips)                # an IP nobody else uses, so only the device links them
    with db.transaction() as s:                                                        # the outsider is now observed on the cluster's device
        s.add(NetworkObservation(observation_id="obs-extra", transaction_id=tx_id, wallet_address=lowest_outsider, ip_address=free_ip, device_id=device,
                                 session_id="sess-extra", origin="synthetic_network_observation", is_synthetic=True))
    r = cl.refresh_clusters(db)
    assert (r.created, r.removed) == (0, 0) and r.updated >= 1                         # same cluster, not a new one
    with db.session() as s:
        after = s.get(EntityCluster, cid)
        assert after is not None and after.wallet_count == count + 1 and after.created_at == created and after.updated_at > updated
        assert lowest_outsider in set(s.scalars(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == cid, EntityClusterMember.entity_type == "wallet")))
        assert s.get(EntityCluster, f"NET-{lowest_outsider}") is None


def test_vanished_clusters_are_removed_with_their_members(networked_analysed_db):
    db = networked_analysed_db
    with db.transaction() as s:
        s.query(NetworkObservation).delete()
    r = cl.refresh_clusters(db)
    assert r.network_clusters == 0 and r.removed >= 1
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(EntityCluster).where(EntityCluster.method == cl.METHOD_NET)) == 0
        assert s.scalar(select(func.count()).select_from(EntityClusterMember).where(EntityClusterMember.cluster_id.like("NET-%"))) == 0
        assert s.scalar(select(func.count()).select_from(EntityCluster).where(EntityCluster.method == cl.METHOD_TXC)) > 0     # other method untouched


def _c(method, *wallets):
    return cl.ClusterData(method, sorted(wallets))


def test_stable_id_assignment_rules():
    N, T = cl.METHOD_NET, cl.METHOD_TXC
    existing = {"NET-c": (N, {"c", "d", "e"}), "TXC-c": (T, {"c", "d"})}
    # growth with a lower wallet: keeps the id of the cluster it overlaps
    grown = [_c(N, "a", "c", "d", "e")]
    cl.assign_stable_ids(grown, existing)
    assert grown[0].cluster_id == "NET-c"
    # the method matters: a NET cluster never takes a TXC id
    other = [_c(T, "c", "d", "x")]
    cl.assign_stable_ids(other, existing)
    assert other[0].cluster_id == "TXC-c"
    # merge: two old clusters become one; the larger overlap keeps its id, the other id disappears
    merged = [_c(N, "c", "d", "e", "m", "n")]
    cl.assign_stable_ids(merged, {"NET-c": (N, {"c", "d", "e"}), "NET-m": (N, {"m", "n"})})
    assert merged[0].cluster_id == "NET-c"
    # split: the larger part keeps the id, the smaller part gets a fresh id from its lowest wallet
    parts = [_c(N, "c", "d"), _c(N, "a", "e")]
    cl.assign_stable_ids(parts, {"NET-c": (N, {"c", "d", "e", "a"})})
    ids = {tuple(p.wallets): p.cluster_id for p in parts}
    assert set(ids.values()) == {"NET-c", "NET-a"} and len(set(ids.values())) == 2
    # a brand-new cluster: id from its lowest wallet, with a suffix if that id is already in use by another cluster
    fresh = [_c(N, "q", "r"), _c(N, "z", "y")]
    cl.assign_stable_ids(fresh, {"NET-y": (N, {"unrelated1", "unrelated2"})})
    assert fresh[0].cluster_id == "NET-q" and fresh[1].cluster_id == "NET-y-2"
    # ids are unique in every case above
    assert len({p.cluster_id for p in parts + fresh}) == 4


def test_analysis_refreshes_clusters_and_a_clustering_failure_never_fails_the_analysis(networked_db, monkeypatch):
    summary = run_analysis(networked_db)
    assert summary.status == "completed" and summary.clusters_total > 0 and summary.cluster_error is None

    def boom(*_a, **_k):
        raise RuntimeError("clustering exploded")

    monkeypatch.setattr("backend.analysis.clustering.refresh_clusters", boom)
    failed = run_analysis(networked_db)
    assert failed.status == "completed" and failed.clusters_total == 0 and "clustering exploded" in failed.cluster_error


# ---- API ---------------------------------------------------------------------------------------------------------------------
def test_cluster_list_filters_and_sorting(networked_client):
    body = networked_client.get("/api/clusters", params={"limit": 500}).json()
    assert body["total"] == len(body["items"]) > 2
    for c in body["items"]:
        assert c["method_label"] and c["wallet_count"] >= 2 and c["created_at"].endswith("Z")
    net = networked_client.get("/api/clusters", params={"method": "shared_network_observation"}).json()
    txc = networked_client.get("/api/clusters", params={"method": "transaction_community"}).json()
    assert net["total"] + txc["total"] == body["total"] and net["total"] > 0 and txc["total"] > 0
    assert all(c["method"] == "shared_network_observation" for c in net["items"])
    big = networked_client.get("/api/clusters", params={"min_wallets": 5}).json()
    assert all(c["wallet_count"] >= 5 for c in big["items"])
    by_size = [c["wallet_count"] for c in networked_client.get("/api/clusters", params={"sort": "size", "limit": 500}).json()["items"]]
    assert by_size == sorted(by_size, reverse=True)
    by_priority = networked_client.get("/api/clusters", params={"limit": 500}).json()["items"]
    lead_counts = [c["priority_summary"]["lead_count"] for c in by_priority]
    assert lead_counts == sorted(lead_counts, reverse=True)
    with_leads = networked_client.get("/api/clusters", params={"has_leads": "true"}).json()
    assert with_leads["total"] > 0 and all(c["priority_summary"]["lead_count"] > 0 for c in with_leads["items"])
    one = body["items"][0]
    member = networked_client.get(f"/api/clusters/{one['cluster_id']}").json()["wallets"][0]["wallet_address"]
    found = networked_client.get("/api/clusters", params={"q": member}).json()
    assert one["cluster_id"] in {c["cluster_id"] for c in found["items"]}
    assert networked_client.get("/api/clusters", params={"q": one["cluster_id"]}).json()["total"] >= 1
    page = networked_client.get("/api/clusters", params={"limit": 2, "offset": 1}).json()
    assert len(page["items"]) == 2 and page["total"] == body["total"]


@pytest.mark.parametrize("params", [{"method": "gang"}, {"sort": "danger"}, {"min_wallets": 1}, {"limit": 0}, {"source": "x"}])
def test_bad_cluster_parameters_are_422(networked_client, params):
    assert networked_client.get("/api/clusters", params=params).status_code == 422


def test_cluster_detail_has_members_relationships_and_a_renderable_graph(networked_client):
    net = networked_client.get("/api/clusters", params={"method": "shared_network_observation", "sort": "size", "limit": 1}).json()["items"][0]
    d = networked_client.get(f"/api/clusters/{net['cluster_id']}").json()
    assert d["cluster_id"] == net["cluster_id"] and len(d["wallets"]) == net["wallet_count"]
    assert d["devices"] and d["ip_addresses"] and "not common ownership" in d["note"] and "not evidence of wrongdoing" in d["note"]
    assert {w["wallet_address"] for w in d["wallets"]} == {n["data"]["address"] for n in d["graph"]["nodes"] if n["type"] == "wallet"}
    assert {"wallet", "device", "ip_observation"} <= set(d["graph"]["counts"]["nodes"])
    assert d["graph"]["counts"]["edges"].get("same_device", 0) >= 1                                # wallets on a shared device are linked
    assert all(n["data"].get("synthetic") for n in d["graph"]["nodes"] if n["type"] in ("device", "ip_observation"))
    ranks = [w["priority_rank"] for w in d["wallets"] if w["priority_rank"]]
    assert ranks == sorted(ranks) and all(w["transaction_count"] >= 0 for w in d["wallets"])
    for r in d["internal_relationships"]:
        assert r["source"] in {w["wallet_address"] for w in d["wallets"]} and r["target"] in {w["wallet_address"] for w in d["wallets"]} and r["transfers"] >= 1

    txc = networked_client.get("/api/clusters", params={"method": "transaction_community", "limit": 1}).json()["items"][0]
    dt = networked_client.get(f"/api/clusters/{txc['cluster_id']}").json()
    assert dt["devices"] == [] and dt["internal_relationships"] and set(dt["graph"]["counts"]["nodes"]) == {"wallet"}
    assert networked_client.get("/api/clusters/NET-nobody").status_code == 404


def test_cluster_wording_never_accuses(networked_client):
    text = (networked_client.get("/api/clusters", params={"limit": 500}).text + networked_client.get("/api/graph", params={"leads": "true"}).text).lower()
    for word in ("criminal organi", "gang", "syndicate", "mafia", "cartel", "ring of", "conspir", "guilty"):
        assert word not in text
    detail = networked_client.get("/api/clusters/" + networked_client.get("/api/clusters").json()["items"][0]["cluster_id"]).text.lower()
    assert "guilty" not in detail and "not common ownership" in detail


def test_refresh_endpoint(networked_client):
    r = networked_client.post("/api/clusters/refresh").json()
    assert (r["created"], r["updated"], r["removed"]) == (0, 0, 0) and r["network_clusters"] > 0
    assert networked_client.get("/api/overview").json()["clusters_total"] == r["network_clusters"] + r["transaction_communities"]


# ---- the project dataset ---------------------------------------------------------------------------------------------------------
def test_project_dataset_clusters(real_db):
    with real_db.session() as s:
        net = list(s.scalars(select(EntityCluster).where(EntityCluster.method == cl.METHOD_NET)))
        txc = list(s.scalars(select(EntityCluster).where(EntityCluster.method == cl.METHOD_TXC)))
    assert len(net) == 33 and sum(c.wallet_count for c in net) == 98 and max(c.wallet_count for c in net) == 6
    assert 10 <= len(txc) <= 40 and sum(c.wallet_count for c in txc) == 410                       # communities partition all wallets
    top = max(txc, key=lambda c: c.priority_summary["lead_count"])
    assert top.priority_summary["analysed"] and top.priority_summary["lead_count"] >= 3
