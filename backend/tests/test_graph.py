"""Relationship graph API: focus modes, node/edge types, filters, caps, synthetic labelling."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest
from sqlalchemy import func, or_, select

from backend.ingestion.base import SourcedTransaction
from backend.models import NetworkObservation, Transaction
from backend.schemas import TransactionIn
from backend.services.ingest import ingest

NODE_TYPES = {"wallet", "transaction", "ip_observation", "device", "session"}
EDGE_TYPES = {"sent_to", "received_from", "observed_from", "associated_with", "same_device", "same_session"}


def g(client, **params):
    r = client.get("/api/graph", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def partners(db, wallet):
    with db.session() as s:
        rows = s.execute(select(Transaction.sender_wallet, Transaction.receiver_wallet).where(or_(Transaction.sender_wallet == wallet, Transaction.receiver_wallet == wallet))).all()
    return {b if a == wallet else a for a, b in rows}


# ---- wallet focus ----------------------------------------------------------------------------------------
def test_wallet_focus_default_is_wallets_and_their_transfers(networked_client, networked_analysed_db):
    out = g(networked_client, wallet="w010")
    assert {n["type"] for n in out["nodes"]} == {"wallet"}
    assert {n["data"]["address"] for n in out["nodes"]} == {"w010"} | partners(networked_analysed_db, "w010")
    focus = [n for n in out["nodes"] if n["data"]["is_focus"]]
    assert [n["id"] for n in focus] == ["wallet:w010"]
    assert out["focus"] == {"type": "wallet", "id": "w010"} and not out["truncated"]
    assert set(out["counts"]["nodes"]) == {"wallet"} and set(out["counts"]["edges"]) <= {"sent_to", "same_device", "same_session"}


def test_sent_to_edges_are_directed_and_aggregated_exactly(networked_client, networked_analysed_db):
    out = g(networked_client, wallet="w000")                            # the hub
    ids = {n["data"]["address"] for n in out["nodes"]}
    with networked_analysed_db.session() as s:
        truth = {(a, b): (n, round(btc, 8)) for a, b, n, btc in s.execute(
            select(Transaction.sender_wallet, Transaction.receiver_wallet, func.count(), func.sum(Transaction.amount_btc))
            .where(Transaction.sender_wallet.in_(ids), Transaction.receiver_wallet.in_(ids)).group_by(Transaction.sender_wallet, Transaction.receiver_wallet))}
    sent = {(e["source"][7:], e["target"][7:]): (e["data"]["transfers"], e["data"]["total_btc"]) for e in out["edges"] if e["type"] == "sent_to"}
    assert sent == truth and sent


def test_wallet_nodes_carry_the_analysis(networked_client):
    hub = next(n for n in g(networked_client, wallet="w000")["nodes"] if n["id"] == "wallet:w000")
    d = hub["data"]
    assert d["is_lead"] is True and d["ml_prediction"] in ("Anomalous", "Normal") and d["priority_level"] in ("High", "Medium", "Low")
    assert d["priority_rank"] >= 1 and 0 <= d["combined_score"] <= 1 and d["transaction_count"] > 0 and d["source"] == "synthetic"


def test_depth_and_truncation(networked_client):
    d0, d1, d2 = (len(g(networked_client, wallet="w010", depth=d)["nodes"]) for d in (0, 1, 2))
    assert d0 == 1 and d0 < d1 <= d2
    small = g(networked_client, wallet="w000", max_nodes=10)
    assert len(small["nodes"]) == 10 and small["truncated"] is True and small["omitted"]["wallet"] > 0
    full = g(networked_client, wallet="w000", max_nodes=500)
    assert not full["truncated"] and len(full["nodes"]) == 1 + len(partners_of(networked_client, "w000"))


def partners_of(client, wallet):
    return {n["data"]["address"] for n in g(client, wallet=wallet, max_nodes=500)["nodes"]} - {wallet}


# ---- node type filters: transactions and infrastructure ------------------------------------------------------------
def test_transaction_nodes_replace_aggregated_wallet_edges(networked_client, networked_analysed_db):
    out = g(networked_client, wallet="w010", node_types="wallet,transaction")
    txs = [n for n in out["nodes"] if n["type"] == "transaction"]
    wallet_ids = {n["data"]["address"] for n in out["nodes"] if n["type"] == "wallet"}
    with networked_analysed_db.session() as s:
        expected = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.sender_wallet.in_(wallet_ids), Transaction.receiver_wallet.in_(wallet_ids)))
    assert len(txs) == expected
    kinds = Counter(e["type"] for e in out["edges"])
    assert kinds["sent_to"] >= len(txs) and kinds["received_from"] == len(txs)              # every transaction: sender -> tx, receiver -> tx
    assert not any(e["type"] == "sent_to" and e["target"].startswith("wallet:") for e in out["edges"])
    by_tx = defaultdict(dict)
    for e in out["edges"]:
        if e["type"] in ("sent_to", "received_from") and e["target"].startswith("tx:"):
            by_tx[e["target"]][e["type"]] = e["source"]
    with networked_analysed_db.session() as s:
        for tid, parts in list(by_tx.items())[:25]:
            t = s.get(Transaction, tid[3:])
            assert parts["sent_to"] == f"wallet:{t.sender_wallet}" and parts["received_from"] == f"wallet:{t.receiver_wallet}"


def test_transaction_cap(networked_client):
    out = g(networked_client, wallet="w000", node_types="wallet,transaction", max_transactions=7)
    assert len([n for n in out["nodes"] if n["type"] == "transaction"]) == 7 and out["truncated"] and out["omitted"]["transaction"] > 0


def test_infrastructure_is_synthetic_and_linked_by_the_documented_edge_types(networked_client):
    out = g(networked_client, wallet="w010", node_types="wallet,ip_observation,device")
    types = {n["type"] for n in out["nodes"]}
    assert {"wallet", "ip_observation", "device"} <= types and "session" not in types
    edge_types = {e["type"] for e in out["edges"]}
    assert {"observed_from", "associated_with"} <= edge_types
    node_by_id = {n["id"]: n for n in out["nodes"]}
    for e in out["edges"]:
        s, t = node_by_id[e["source"]]["type"], node_by_id[e["target"]]["type"]
        if e["type"] == "observed_from":
            assert (s, t) == ("wallet", "ip_observation")
        if e["type"] == "associated_with":
            assert (s, t) == ("ip_observation", "device")
    for n in out["nodes"]:
        if n["type"] in ("ip_observation", "device"):
            assert n["data"]["synthetic"] is True and "Synthetic network observation" in n["data"]["note"]
    assert any("do not contain IP addresses" in note for note in out["notes"])


def test_transaction_level_infrastructure_and_sessions(networked_client):
    out = g(networked_client, wallet="w010", node_types="wallet,transaction,ip_observation,device,session")
    node_by_id = {n["id"]: n for n in out["nodes"]}
    pairs = {(node_by_id[e["source"]]["type"], e["type"], node_by_id[e["target"]]["type"]) for e in out["edges"]}
    assert ("transaction", "observed_from", "ip_observation") in pairs
    assert ("ip_observation", "associated_with", "device") in pairs and ("session", "associated_with", "device") in pairs
    assert ("transaction", "associated_with", "session") in pairs
    assert set(out["counts"]["nodes"]) <= NODE_TYPES and set(out["counts"]["edges"]) <= EDGE_TYPES
    sessions = [n for n in out["nodes"] if n["type"] == "session"]
    assert sessions and all(n["data"]["observation_count"] >= 1 and n["data"]["started_at"] for n in sessions)


def test_derived_same_device_edges_match_the_shared_devices(networked_client, networked_analysed_db):
    with networked_analysed_db.session() as s:
        dev_w = defaultdict(set)
        for w, d in s.execute(select(NetworkObservation.wallet_address, NetworkObservation.device_id)):
            dev_w[d].add(w)
    shared = {d: w for d, w in dev_w.items() if len(w) > 1}
    assert shared
    device, wallets = next(iter(shared.items()))
    seed = sorted(wallets)[0]
    out = g(networked_client, wallet=seed, depth=1, max_nodes=500)
    included = {n["data"]["address"] for n in out["nodes"]}
    expected = {frozenset((a, b)) for d, ws in dev_w.items() for a in ws for b in ws if a < b and a in included and b in included}
    got = {frozenset((e["source"][7:], e["target"][7:])) for e in out["edges"] if e["type"] == "same_device"}
    assert got == expected and got
    e = next(e for e in out["edges"] if e["type"] == "same_device")
    assert e["data"]["synthetic"] is True and e["data"]["device_id"].startswith("dev-")


def test_edge_type_filter(networked_client):
    out = g(networked_client, wallet="w010", node_types="wallet,ip_observation,device", edge_types="associated_with")
    assert {e["type"] for e in out["edges"]} == {"associated_with"}
    only_transfers = g(networked_client, wallet="w010", edge_types="sent_to")
    assert {e["type"] for e in only_transfers["edges"]} == {"sent_to"}
    nodes = {n["id"] for n in out["nodes"]}
    assert all(e["source"] in nodes and e["target"] in nodes for e in out["edges"])


# ---- other focuses ----------------------------------------------------------------------------------------------------------
def test_transaction_focus(networked_client, networked_analysed_db):
    with networked_analysed_db.session() as s:
        obs = s.scalars(select(NetworkObservation).limit(1)).one()
        tx = s.get(Transaction, obs.transaction_id)
        sender, receiver, tid = tx.sender_wallet, tx.receiver_wallet, tx.transaction_id
    out = g(networked_client, transaction=tid, depth=0, node_types="wallet,transaction,ip_observation,device")
    ids = {n["id"] for n in out["nodes"]}
    assert {f"wallet:{sender}", f"wallet:{receiver}", f"tx:{tid}"} <= ids
    assert next(n for n in out["nodes"] if n["id"] == f"tx:{tid}")["data"]["is_focus"] is True
    assert f"ip:{obs.ip_address}" in ids and f"device:{obs.device_id}" in ids
    assert any(e["type"] == "observed_from" and e["source"] == f"tx:{tid}" for e in out["edges"])
    assert out["focus"] == {"type": "transaction", "id": tid}


def test_cluster_focus_shows_the_members(networked_client):
    net = networked_client.get("/api/clusters", params={"method": "shared_network_observation", "limit": 1}).json()["items"][0]
    out = g(networked_client, cluster=net["cluster_id"], depth=0, node_types="wallet,device,ip_observation")
    assert len([n for n in out["nodes"] if n["type"] == "wallet"]) == net["wallet_count"]
    assert out["focus"]["type"] == "cluster" and {"device", "ip_observation"} <= {n["type"] for n in out["nodes"]}


def test_leads_overview_focus(networked_client):
    leads = networked_client.get("/api/leads", params={"limit": 3}).json()["items"]
    out = g(networked_client, leads="true", lead_limit=3, depth=0)
    assert {n["data"]["address"] for n in out["nodes"]} == {l["wallet_address"] for l in leads}
    wider = g(networked_client, leads="true", lead_limit=3, depth=1)
    assert len(wider["nodes"]) > len(out["nodes"]) and all(n["data"].get("is_lead") is not None for n in wider["nodes"])


# ---- validation and safety ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("params", [
    {}, {"wallet": "w001", "transaction": "syn-000001"}, {"wallet": "w001", "leads": "true"},
    {"wallet": "w001", "node_types": "wallet,router"}, {"wallet": "w001", "edge_types": "knows"},
    {"wallet": "w001", "depth": 9}, {"wallet": "w001", "max_nodes": 1}, {"wallet": "w001", "max_nodes": 5000},
])
def test_bad_graph_requests_are_422(networked_client, params):
    r = networked_client.get("/api/graph", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] in ("graph_focus_required", "validation_error")


@pytest.mark.parametrize("params", [{"wallet": "nobody"}, {"transaction": "syn-999999"}, {"cluster": "NET-nobody"}])
def test_unknown_focus_is_404(networked_client, params):
    assert networked_client.get("/api/graph", params=params).status_code == 404


def test_real_bitcoin_wallets_have_no_network_infrastructure(networked_analysed_db, settings):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with networked_analysed_db.transaction() as s:
        ingest(s, [SourcedTransaction(TransactionIn(transaction_id=f"real{i}", sender_wallet=f"bc1{i}", receiver_wallet=f"bc1{i + 1}", timestamp="2026-05-01T00:00:00", amount_btc=1))
                   for i in range(4)], "real_bitcoin")
    with TestClient(create_app(settings)) as c:
        out = c.get("/api/graph", params={"wallet": "bc11", "node_types": "wallet,transaction,ip_observation,device,session"}).json()
    types = Counter(n["type"] for n in out["nodes"])
    assert types["wallet"] >= 2 and types["transaction"] >= 1
    assert not (set(types) & {"ip_observation", "device", "session"}) and not {"observed_from", "same_device", "same_session"} & {e["type"] for e in out["edges"]}


# ---- the whole live path ------------------------------------------------------------------------------------------------------------
def test_a_posted_transaction_appears_in_the_graph_with_its_observation_and_updates_clusters(networked_client, networked_analysed_db):
    monitor = networked_client.app.state.monitor
    tid = None
    for i in range(30):                                            # observation coverage is probabilistic; a few posts at most
        r = networked_client.post("/api/transactions", json={"sender_wallet": "w010", "receiver_wallet": "w011", "timestamp": f"2026-09-2{i % 5}T0{i % 9}:30:00Z", "amount_btc": 0.5 + i})
        assert r.status_code == 201
        tid = r.json()["transaction_id"]
        if networked_client.get("/api/network/observations", params={"transaction_id": tid}).json()["total"]:
            break
    obs = networked_client.get("/api/network/observations", params={"transaction_id": tid}).json()["items"]
    assert obs and all(o["is_synthetic"] and o["observed_party"] in ("sender", "receiver") for o in obs)
    out = g(networked_client, transaction=tid, depth=0, node_types="wallet,transaction,ip_observation,device")
    assert any(e["type"] == "observed_from" and e["source"] == f"tx:{tid}" for e in out["edges"])
    tick = monitor.tick()
    assert tick["analysis"]["status"] == "completed" and tick["analysis"]["clusters_total"] > 0
    assert networked_client.get("/api/overview").json()["network_observations_total"] > 0
