"""Unified search."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.models import EntityClusterMember, NetworkObservation, Transaction, Wallet


def s(client, q, **params):
    r = client.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_result_shape_and_totals(networked_client):
    out = s(networked_client, "w00")
    assert out["query"] == "w00" and set(out["categories"]) == {"wallets", "transactions", "clusters", "ip_observations", "devices", "sessions", "cases"}
    assert out["total"] == sum(c["total"] for c in out["categories"].values())
    for cat in out["categories"].values():
        assert cat["total"] >= len(cat["items"])


def test_wallet_search_puts_the_exact_match_first_and_shows_lead_state(networked_client):
    out = s(networked_client, "w000", types="wallets")["categories"]["wallets"]
    assert out["items"][0]["id"] == "w000" and out["items"][0]["match"] == "wallet address"
    d = out["items"][0]["data"]
    assert d["is_lead"] is True and d["priority_level"] in ("High", "Medium", "Low") and d["case_ids"] == []
    assert "lead, priority #" in out["items"][0]["subtitle"]
    assert s(networked_client, "W000", types="wallets")["categories"]["wallets"]["items"][0]["id"] == "w000"        # case-insensitive
    total = networked_client.get("/api/wallets", params={"q": "w01", "limit": 500}).json()["total"]
    assert s(networked_client, "w01", types="wallets")["categories"]["wallets"]["total"] == total


def test_limit_caps_the_hits_but_not_the_reported_total(networked_client):
    cat = s(networked_client, "w0", types="wallets", limit=3)["categories"]["wallets"]
    assert len(cat["items"]) == 3 and cat["total"] > 3


def test_transaction_search(networked_client, networked_analysed_db):
    with networked_analysed_db.session() as db:
        t = db.scalars(select(Transaction).limit(1)).one()
    cat = s(networked_client, t.transaction_id, types="transactions")["categories"]["transactions"]
    assert cat["items"][0]["id"] == t.transaction_id and cat["items"][0]["match"] == "transaction ID"
    assert f"{t.sender_wallet} → {t.receiver_wallet}" in cat["items"][0]["subtitle"] and cat["items"][0]["data"]["sender"] == t.sender_wallet
    assert s(networked_client, "syn-0000", types="transactions")["categories"]["transactions"]["total"] > 1


def test_cluster_search_by_id_and_by_member_wallet(networked_client, networked_analysed_db):
    first = networked_client.get("/api/clusters", params={"method": "shared_network_observation"}).json()["items"][0]["cluster_id"]
    by_id = s(networked_client, first, types="clusters")["categories"]["clusters"]
    assert by_id["items"][0]["id"] == first and by_id["items"][0]["match"] == "cluster ID"
    with networked_analysed_db.session() as db:
        wallet = db.scalar(select(EntityClusterMember.entity_id).where(EntityClusterMember.cluster_id == first, EntityClusterMember.entity_type == "wallet").order_by(EntityClusterMember.entity_id.desc()))
    by_member = s(networked_client, wallet, types="clusters")["categories"]["clusters"]
    assert first in {h["id"] for h in by_member["items"]} and {h["match"] for h in by_member["items"] if h["id"] == first} <= {"member wallet", "cluster ID"}
    assert all(h["type"] == "cluster" for h in by_member["items"])


def test_network_observation_search_ip_observation_id_device_session(networked_client, networked_analysed_db):
    with networked_analysed_db.session() as db:
        o = db.scalars(select(NetworkObservation).limit(1)).one()
        n_ip = db.scalar(select(func.count()).select_from(NetworkObservation).where(NetworkObservation.ip_address == o.ip_address))
        n_dev = db.scalar(select(func.count()).select_from(NetworkObservation).where(NetworkObservation.device_id == o.device_id))
    ip = s(networked_client, o.ip_address, types="ip_observations")
    hit = ip["categories"]["ip_observations"]["items"][0]
    assert (hit["type"], hit["id"], hit["match"]) == ("ip", o.ip_address, "IP address") and hit["data"]["observation_count"] == n_ip and hit["data"]["synthetic"] is True
    assert any("Synthetic network observation" in note for note in ip["notes"])
    obs = s(networked_client, o.observation_id, types="ip_observations")["categories"]["ip_observations"]["items"][0]
    assert (obs["type"], obs["id"], obs["match"]) == ("ip_observation", o.observation_id, "observation ID") and obs["data"]["wallet_address"] == o.wallet_address
    dev = s(networked_client, o.device_id, types="devices")["categories"]["devices"]["items"][0]
    assert dev["id"] == o.device_id and dev["data"]["observation_count"] == n_dev and dev["match"] == "device ID"
    sess = s(networked_client, o.session_id, types="sessions")["categories"]["sessions"]["items"][0]
    assert sess["id"] == o.session_id


def test_case_search_by_id_title_and_contained_item(networked_client):
    case = networked_client.post("/api/cases", json={"title": "Fan-in review", "items": [{"type": "wallet", "id": "w005"}]}).json()["case_id"]
    for q in (case, "fan-in", "w005"):
        cat = s(networked_client, q, types="cases")["categories"]["cases"]
        assert case in {h["id"] for h in cat["items"]}
    hit = s(networked_client, case, types="cases")["categories"]["cases"]["items"][0]
    assert hit["match"] == "case ID" and "Fan-in review" in hit["subtitle"] and hit["data"]["status"] == "Open"
    assert s(networked_client, "w005", types="wallets")["categories"]["wallets"]["items"][0]["data"]["case_ids"] == [case]


def test_types_filter_and_notes(networked_client):
    only = s(networked_client, "w00", types="wallets,cases")
    assert set(only["categories"]) == {"wallets", "cases"} and only["notes"] == []            # no network categories: no synthetic disclaimer
    assert s(networked_client, "dev-", types="devices")["notes"]


def test_text_is_matched_literally(loaded_client):
    assert s(loaded_client, "%%", types="wallets")["total"] == 0
    assert s(loaded_client, "wallet_", types="wallets")["categories"]["wallets"]["total"] == 5            # '_' is a literal character here
    assert s(loaded_client, "w_l", types="wallets")["categories"]["wallets"]["total"] == 0               # not a wildcard
    injected = s(loaded_client, "'; DROP TABLE wallets;--")
    assert injected["total"] == 0 and loaded_client.get("/api/overview").json()["wallets_total"] == 5


@pytest.mark.parametrize("params", [{"q": ""}, {"q": "a"}, {"q": " a "}, {"q": "ab", "types": "people"}, {"q": "ab", "limit": 0}, {"q": "ab", "limit": 51}, {"q": "x" * 129}])
def test_bad_search_requests_are_422(networked_client, params):
    r = networked_client.get("/api/search", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_searching_an_empty_database_is_fine(client):
    out = s(client, "anything")
    assert out["total"] == 0 and all(c["items"] == [] for c in out["categories"].values())


def test_project_dataset_searches(real_client):
    top = s(real_client, "wallet_350", types="wallets")["categories"]["wallets"]["items"][0]
    assert top["id"] == "wallet_350" and top["data"]["is_lead"] and top["data"]["priority_level"] == "High" and top["data"]["ml_prediction"] == "Anomalous"
    dev = s(real_client, "dev-0318", types="devices")["categories"]["devices"]["items"][0]
    assert dev["id"] == "dev-0318" and dev["data"]["observation_count"] == 46 and dev["data"]["wallet_count"] == 1
    clusters = s(real_client, "wallet_350", types="clusters")["categories"]["clusters"]
    assert clusters["total"] >= 1 and clusters["items"][0]["data"]["wallet_count"] >= 2
    assert s(real_client, "syn-004997", types="transactions")["categories"]["transactions"]["items"][0]["id"] == "syn-004997"
