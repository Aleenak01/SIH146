"""Case management: creation, items and evidence snapshots, history, notes, updates, review state, related data."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.models import Case, CaseHistory, CaseItem, Transaction

from .conftest import SMALL_TRANSFERS, write_raw_csv


def actions(case_json):
    return [h["action"] for h in case_json["history"]]


def top_lead(client):
    return client.get("/api/leads").json()["items"][0]["wallet_address"]


def first_cluster(client, method=None):
    params = {"method": method} if method else {}
    return client.get("/api/clusters", params=params).json()["items"][0]["cluster_id"]


# ---- a lead is never a case by itself --------------------------------------------------------------------------
def test_no_case_exists_until_an_investigator_creates_one(networked_client):
    assert networked_client.get("/api/cases").json()["total"] == 0
    leads = networked_client.get("/api/leads", params={"limit": 500}).json()["items"]
    assert leads and all(l["is_case"] is False and l["case_ids"] == [] and l["review_status"] == "Unreviewed" for l in leads)
    networked_client.post("/api/transactions", json={"sender_wallet": "w001", "receiver_wallet": "w002", "timestamp": "2026-09-01T00:00:00Z", "amount_btc": 1})
    tick = networked_client.app.state.monitor.tick()                              # a whole new analysis with new leads
    assert tick["analysis"]["status"] == "completed"
    assert networked_client.get("/api/cases").json()["total"] == 0
    ov = networked_client.get("/api/overview").json()
    assert (ov["cases_total"], ov["active_cases"]) == (0, 0) and ov["leads_total"] > 0


# ---- creating ----------------------------------------------------------------------------------------------------------
def test_minimal_case_and_sequential_ids(networked_client):
    a = networked_client.post("/api/cases", json={"title": "  First look  "})
    assert a.status_code == 201
    body = a.json()
    assert (body["case_id"], body["title"], body["status"], body["priority"], body["assigned_to"]) == ("CASE-0001", "First look", "Open", None, None)
    assert body["items"] == [] and actions(body) == ["case_created"] and body["history"][0]["actor"] == "Investigator"
    assert "does not establish that any wrongdoing" in body["disclaimer"]
    assert body["related_transactions"] == {"total": 0, "value_moved_btc": 0.0, "first_at": None, "last_at": None}
    assert networked_client.post("/api/cases", json={"title": "Second"}).json()["case_id"] == "CASE-0002"


def test_ids_keep_counting_and_are_unique(networked_client):
    ids = [networked_client.post("/api/cases", json={"title": f"c{i}"}).json()["case_id"] for i in range(12)]
    assert ids == [f"CASE-{i:04d}" for i in range(1, 13)]


@pytest.mark.parametrize("bad", [
    {}, {"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"title": "t", "priority": "Urgent"}, {"title": "t", "status": "Closed"},
    {"title": "t", "assigned_to": "x" * 81}, {"title": "t", "description": "d" * 5001}, {"title": "t", "items": [{"type": "person", "id": "x"}]},
    {"title": "t", "items": [{"type": "wallet", "id": ""}]}, {"title": "t", "items": [{"type": "wallet", "id": "w001", "extra": 1}]},
    {"title": "t", "items": [{"type": "wallet", "id": "w001"}, {"type": "wallet", "id": "w001"}]},
    {"title": "t", "items": [{"type": "wallet", "id": f"w{i}"} for i in range(51)]}, {"title": "t", "note": "n" * 5001}, {"title": "t", "unknown": 1},
])
def test_invalid_case_requests_are_rejected_and_create_nothing(networked_client, bad):
    r = networked_client.post("/api/cases", json=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert networked_client.get("/api/cases").json()["total"] == 0


def test_case_from_a_lead_with_every_kind_of_item(networked_client, networked_analysed_db):
    lead = top_lead(networked_client)
    with networked_analysed_db.session() as s:
        tx_id = s.scalar(select(Transaction.transaction_id).where(Transaction.receiver_wallet == lead).limit(1))
    cluster = first_cluster(networked_client, "shared_network_observation")
    r = networked_client.post("/api/cases", json={
        "title": "Hub review", "description": "Many senders converge on one wallet.", "priority": "High", "assigned_to": "A. Analyst", "note": "opening note",
        "items": [{"type": "lead", "id": lead, "note": "top of the queue"}, {"type": "wallet", "id": "w010"}, {"type": "transaction", "id": tx_id}, {"type": "cluster", "id": cluster}]})
    assert r.status_code == 201
    d = r.json()
    assert d["item_counts"] == {"lead": 1, "wallet": 1, "transaction": 1, "cluster": 1, "entity": 0} and d["priority"] == "High" and d["assigned_to"] == "A. Analyst"
    assert actions(d) == ["case_created", "lead_added", "wallet_added", "transaction_added", "cluster_added", "note_added"]
    assert "top of the queue" in d["history"][1]["detail"] and d["notes"][0]["detail"] == "opening note"
    snaps = {i["item_type"]: i["evidence_snapshot"] for i in d["items"]}
    assert snaps["lead"]["kind"] == "lead" and snaps["lead"]["reasons"] and snaps["lead"]["findings"] and "not statistically validated" in snaps["lead"]["contributing_evidence"]["fusion"]["label"]
    assert snaps["wallet"]["kind"] == "wallet" and snaps["wallet"]["analysis"]["scored"] and snaps["wallet"]["wallet"]["address"] == "w010"
    assert snaps["transaction"]["transaction"]["transaction_id"] == tx_id and all(o["is_synthetic"] for o in snaps["transaction"]["synthetic_network_observations"])
    assert snaps["cluster"]["cluster_id"] == cluster and snaps["cluster"]["wallets"] and "sessions" not in snaps["cluster"] and snaps["cluster"]["devices"]
    assert all(i["evidence_snapshot"]["taken_at"].endswith("Z") for i in d["items"])
    assert d["wallets"] == sorted({lead, "w010"})


def test_creation_is_all_or_nothing(networked_client):
    r = networked_client.post("/api/cases", json={"title": "Bad", "items": [{"type": "wallet", "id": "w001"}, {"type": "wallet", "id": "ghost"}]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert networked_client.get("/api/cases").json()["total"] == 0
    assert networked_client.post("/api/cases", json={"title": "Good"}).json()["case_id"] == "CASE-0001"        # the id was not used up


@pytest.mark.parametrize("item,needle", [
    ({"type": "wallet", "id": "ghost"}, "No wallet"), ({"type": "transaction", "id": "syn-999999"}, "No transaction"),
    ({"type": "cluster", "id": "NET-nobody"}, "No cluster"), ({"type": "lead", "id": "ghost"}, "not a current investigative lead"),
])
def test_unknown_items_are_404(networked_client, item, needle):
    case = networked_client.post("/api/cases", json={"title": "c"}).json()["case_id"]
    r = networked_client.post(f"/api/cases/{case}/items", json=item)
    assert r.status_code == 404 and needle in r.json()["error"]["message"]


def test_a_wallet_that_is_not_a_lead_can_still_be_added_as_a_wallet(networked_client):
    leads = {l["wallet_address"] for l in networked_client.get("/api/leads", params={"limit": 500}).json()["items"]}
    other = next(w["address"] for w in networked_client.get("/api/wallets", params={"limit": 100}).json()["items"] if w["address"] not in leads)
    case = networked_client.post("/api/cases", json={"title": "c"}).json()["case_id"]
    assert networked_client.post(f"/api/cases/{case}/items", json={"type": "lead", "id": other}).status_code == 404
    r = networked_client.post(f"/api/cases/{case}/items", json={"type": "wallet", "id": other})
    assert r.status_code == 201 and r.json()["evidence_snapshot"]["analysis"]["is_lead"] is False


# ---- items ------------------------------------------------------------------------------------------------------------------
def test_add_and_remove_items_with_history(networked_client):
    case = networked_client.post("/api/cases", json={"title": "c"}).json()["case_id"]
    before = networked_client.get(f"/api/cases/{case}").json()["updated_at"]
    r = networked_client.post(f"/api/cases/{case}/items", json={"type": "wallet", "id": "w001", "note": "counterparty of the hub"})
    assert r.status_code == 201 and r.json()["current"]["exists"] is True
    dup = networked_client.post(f"/api/cases/{case}/items", json={"type": "wallet", "id": "w001"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_item"
    d = networked_client.get(f"/api/cases/{case}").json()
    assert actions(d) == ["case_created", "wallet_added"] and "counterparty of the hub" in d["history"][1]["detail"] and d["updated_at"] >= before
    assert networked_client.delete(f"/api/cases/{case}/items/wallet/w001").status_code == 204
    d = networked_client.get(f"/api/cases/{case}").json()
    assert d["items"] == [] and actions(d)[-1] == "item_removed"
    assert networked_client.delete(f"/api/cases/{case}/items/wallet/w001").status_code == 404
    assert networked_client.post("/api/cases/CASE-9999/items", json={"type": "wallet", "id": "w001"}).status_code == 404


def test_snapshots_are_kept_while_current_values_move_on(networked_client):
    monitor = networked_client.app.state.monitor
    case = networked_client.post("/api/cases", json={"title": "drift", "items": [{"type": "wallet", "id": "w010"}]}).json()["case_id"]
    first = networked_client.get(f"/api/cases/{case}").json()
    snap = first["items"][0]["evidence_snapshot"]
    n0 = first["related_transactions"]["total"]
    for i in range(3):
        networked_client.post("/api/transactions", json={"sender_wallet": "w010", "receiver_wallet": f"w0{20 + i}", "timestamp": f"2026-09-0{i + 1}T10:00:00Z", "amount_btc": 2 + i})
    assert monitor.tick()["analysis"]["status"] == "completed"
    later = networked_client.get(f"/api/cases/{case}").json()
    assert later["items"][0]["evidence_snapshot"] == snap                                  # what the investigator saw is unchanged
    assert later["related_transactions"]["total"] == n0 + 3                                # live data moved on
    assert later["items"][0]["current"]["exists"] is True and snap["analysis"]["run_id"] < networked_client.get("/api/wallets/w010/analysis").json()["run_id"]


# ---- updating ----------------------------------------------------------------------------------------------------------------
def test_patch_records_every_change(networked_client):
    case = networked_client.post("/api/cases", json={"title": "Old title"}).json()["case_id"]
    r = networked_client.patch(f"/api/cases/{case}", json={"status": "Under investigation", "priority": "Medium", "assigned_to": "R. Rao", "title": "New title", "description": "Why"})
    assert r.status_code == 200
    d = r.json()
    assert (d["status"], d["priority"], d["assigned_to"], d["title"], d["description"]) == ("Under investigation", "Medium", "R. Rao", "New title", "Why")
    details = {h["action"]: h["detail"] for h in d["history"]}
    assert details["status_changed"] == "Open → Under investigation" and details["priority_changed"] == "none → Medium"
    assert details["assignment_changed"] == "none → R. Rao" and details["title_changed"] == "Old title → New title" and "description_changed" in details
    n = len(d["history"])
    same = networked_client.patch(f"/api/cases/{case}", json={"status": "Under investigation", "priority": "Medium"}).json()
    assert len(same["history"]) == n                                                        # nothing changed, nothing logged
    cleared = networked_client.patch(f"/api/cases/{case}", json={"priority": None, "assigned_to": ""}).json()
    assert cleared["priority"] is None and cleared["assigned_to"] is None and actions(cleared)[-2:] == ["priority_changed", "assignment_changed"]


@pytest.mark.parametrize("bad", [{}, {"status": "Done"}, {"status": None}, {"title": None}, {"title": ""}, {"priority": "Urgent"}, {"unknown": 1}, {"title": "x" * 201}])
def test_bad_patches_are_422(networked_client, bad):
    case = networked_client.post("/api/cases", json={"title": "c"}).json()["case_id"]
    assert networked_client.patch(f"/api/cases/{case}", json=bad).status_code == 422
    assert networked_client.patch("/api/cases/CASE-9999", json={"title": "x"}).status_code == 404


def test_closing_and_reopening_and_the_active_case_count(networked_client):
    a = networked_client.post("/api/cases", json={"title": "a"}).json()["case_id"]
    networked_client.post("/api/cases", json={"title": "b"})
    assert networked_client.get("/api/overview").json()["active_cases"] == 2
    closed = networked_client.patch(f"/api/cases/{a}", json={"status": "Closed"}).json()
    assert closed["history"][-1]["detail"] == "Open → Closed"
    ov = networked_client.get("/api/overview").json()
    assert (ov["cases_total"], ov["active_cases"]) == (2, 1)
    assert networked_client.post(f"/api/cases/{a}/notes", json={"text": "closing remarks"}).status_code == 201       # notes are still allowed
    reopened = networked_client.patch(f"/api/cases/{a}", json={"status": "Open"}).json()
    assert reopened["history"][-1]["detail"] == "Closed → Open" and networked_client.get("/api/overview").json()["active_cases"] == 2


# ---- notes and evidence review -------------------------------------------------------------------------------------------------
def test_notes_and_evidence_review_are_part_of_the_history(networked_client):
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "wallet", "id": "w001"}]}).json()["case_id"]
    n = networked_client.post(f"/api/cases/{case}/notes", json={"text": "Called out three repeated partners."})
    assert n.status_code == 201 and n.json()["action"] == "note_added" and n.json()["actor"] == "Investigator"
    rv = networked_client.post(f"/api/cases/{case}/reviews", json={"item_type": "wallet", "item_id": "w001", "note": "features look consistent"})
    assert rv.status_code == 201 and rv.json()["action"] == "evidence_reviewed" and "features look consistent" in rv.json()["detail"]
    d = networked_client.get(f"/api/cases/{case}").json()
    assert actions(d) == ["case_created", "wallet_added", "note_added", "evidence_reviewed"] and [h["detail"] for h in d["notes"]] == ["Called out three repeated partners."]
    assert networked_client.get(f"/api/cases/{case}/history").json() == d["history"]
    assert [h["entry_id"] for h in d["history"]] == sorted(h["entry_id"] for h in d["history"])
    assert networked_client.post(f"/api/cases/{case}/reviews", json={"item_type": "wallet", "item_id": "w002"}).status_code == 404
    for bad in ({"text": ""}, {"text": "  "}, {"text": "x" * 5001}, {}):
        assert networked_client.post(f"/api/cases/{case}/notes", json=bad).status_code == 422


# ---- reading: list, transactions, graph ----------------------------------------------------------------------------------------------
def test_listing_and_filters(networked_client):
    lead = top_lead(networked_client)
    a = networked_client.post("/api/cases", json={"title": "Hub review", "priority": "High", "items": [{"type": "lead", "id": lead}]}).json()["case_id"]
    b = networked_client.post("/api/cases", json={"title": "Payroll pattern", "priority": "Low", "items": [{"type": "wallet", "id": "w005"}]}).json()["case_id"]
    c = networked_client.post("/api/cases", json={"title": "Closed one"}).json()["case_id"]
    networked_client.patch(f"/api/cases/{c}", json={"status": "Closed"})
    ids = lambda **p: [x["case_id"] for x in networked_client.get("/api/cases", params=p).json()["items"]]
    assert set(ids()) == {a, b, c} and ids(sort="case_id", order="asc") == [a, b, c] and ids(sort="created_at", order="desc")[0] == c
    assert ids(status="Closed") == [c] and set(ids(status="Open")) == {a, b} and ids(priority="High") == [a]
    assert ids(q="payroll") == [b] and ids(q=a) == [a] and ids(q="w005") == [b] and ids(q="%") == []
    assert ids(item_id=lead) == [a] and ids(item_type="wallet", item_id=lead) == [] and ids(item_type="lead", item_id=lead) == [a]
    page = networked_client.get("/api/cases", params={"limit": 2, "offset": 1, "sort": "case_id", "order": "asc"}).json()
    assert page["total"] == 3 and [x["case_id"] for x in page["items"]] == [b, c]
    assert page["items"][0]["item_counts"] == {"lead": 0, "wallet": 1, "transaction": 0, "cluster": 0, "entity": 0}
    for bad in ({"status": "x"}, {"priority": "x"}, {"sort": "title"}, {"limit": 0}, {"item_type": "person"}):
        assert networked_client.get("/api/cases", params=bad).status_code == 422
    assert networked_client.get("/api/cases/CASE-9999").json()["error"]["code"] == "not_found"


def test_case_transactions_and_graph(networked_client, networked_analysed_db):
    lead = top_lead(networked_client)
    with networked_analysed_db.session() as s:
        outside = s.scalars(select(Transaction).where(Transaction.sender_wallet != lead, Transaction.receiver_wallet != lead)).first().transaction_id
        expected = s.scalar(select(func.count()).select_from(Transaction).where((Transaction.sender_wallet == lead) | (Transaction.receiver_wallet == lead)))
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "lead", "id": lead}, {"type": "transaction", "id": outside}]}).json()["case_id"]
    page = networked_client.get(f"/api/cases/{case}/transactions", params={"limit": 500}).json()
    assert page["total"] == expected + 1 and outside in {t["transaction_id"] for t in page["items"]}
    times = [t["timestamp"] for t in page["items"]]
    assert times == sorted(times, reverse=True)
    assert [t["timestamp"] for t in networked_client.get(f"/api/cases/{case}/transactions", params={"order": "asc", "limit": 500}).json()["items"]] == sorted(times)
    g = networked_client.get(f"/api/cases/{case}/graph", params={"node_types": "wallet,device,ip_observation"}).json()
    assert g["focus"] == {"type": "case", "id": case} and {"wallet", "device", "ip_observation"} <= set(g["counts"]["nodes"])
    focus = {n["data"]["address"] for n in g["nodes"] if n["data"].get("is_focus")}
    assert lead in focus
    empty = networked_client.post("/api/cases", json={"title": "empty"}).json()["case_id"]
    assert networked_client.get(f"/api/cases/{empty}/graph").json()["nodes"] == []
    assert networked_client.get(f"/api/cases/{empty}/transactions").json()["total"] == 0


# ---- review status --------------------------------------------------------------------------------------------------------------------------
def test_review_status_flow_and_it_never_touches_the_analysis(networked_client):
    w = top_lead(networked_client)
    before = networked_client.get(f"/api/wallets/{w}/analysis").json()
    assert networked_client.get(f"/api/wallets/{w}/review").json() == {"wallet_address": w, "status": "Unreviewed", "case_ids": []}
    assert networked_client.put(f"/api/wallets/{w}/review", json={"status": "Under Review"}).json()["status"] == "Under Review"
    assert networked_client.put(f"/api/wallets/{w}/review", json={"status": "Under Review"}).json()["status"] == "Under Review"          # idempotent
    assert networked_client.get(f"/api/leads/{w}").json()["review_status"] == "Under Review" and networked_client.get(f"/api/leads/{w}").json()["is_case"] is False
    assert [l["review_status"] for l in networked_client.get("/api/leads", params={"q": w}).json()["items"]] == ["Under Review"]
    assert networked_client.get(f"/api/wallets/{w}/analysis").json() == {**before, "review_status": "Under Review"}                    # only the marker differs
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "lead", "id": w}]}).json()["case_id"]
    assert networked_client.get(f"/api/wallets/{w}/review").json() == {"wallet_address": w, "status": "Case Created", "case_ids": [case]}
    assert networked_client.get(f"/api/leads/{w}").json()["is_case"] is True
    assert networked_client.get(f"/api/wallets/{w}/analysis").json()["case_ids"] == [case]
    networked_client.delete(f"/api/cases/{case}/items/lead/{w}")
    assert networked_client.get(f"/api/wallets/{w}/review").json()["status"] == "Under Review"                                          # back to the marker
    assert networked_client.put(f"/api/wallets/{w}/review", json={"status": "Unreviewed"}).json()["status"] == "Unreviewed"


def test_review_validation(networked_client):
    assert networked_client.put("/api/wallets/w001/review", json={"status": "Case Created"}).status_code == 422       # only derived, never set
    assert networked_client.put("/api/wallets/w001/review", json={"status": "Done"}).status_code == 422
    assert networked_client.put("/api/wallets/ghost/review", json={"status": "Under Review"}).status_code == 404
    assert networked_client.get("/api/wallets/ghost/review").status_code == 404


def test_cluster_knows_which_cases_hold_it(networked_client):
    cid = first_cluster(networked_client)
    assert networked_client.get(f"/api/clusters/{cid}").json()["case_ids"] == []
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "cluster", "id": cid}]}).json()["case_id"]
    assert networked_client.get(f"/api/clusters/{cid}").json()["case_ids"] == [case]


# ---- address entities as a case item (Phase 3 Part B) ------------------------------------------------------------------------
def _seed_entity(client, *, wallet_a="wA", wallet_b="wB") -> str:
    """One rich transaction (2 co-spent addresses) + a built entity, on the same live database the TestClient uses."""
    from datetime import datetime

    from backend.models import Transaction, TxDetails, TxInput, TxOutput, Wallet

    db = client.app.state.db
    with db.transaction() as s:
        for w in (wallet_a, wallet_b):
            if s.get(Wallet, w) is None:
                s.add(Wallet(address=w, source="synthetic"))
        s.flush()
        s.add(Transaction(transaction_id="entity-seed-t1", timestamp=datetime(2026, 1, 1), sender_wallet=wallet_a, receiver_wallet=wallet_b,
                          amount_btc=1.0, input_count=2, output_count=1, source="synthetic"))
        s.flush()
        s.add(TxDetails(transaction_id="entity-seed-t1", txid="e" * 64, fee_btc=0.0001, script_type="p2wpkh", source="synthetic"))
        s.add(TxInput(transaction_id="entity-seed-t1", position=0, address="entity-seed-addr1", amount_btc=0.5))
        s.add(TxInput(transaction_id="entity-seed-t1", position=1, address="entity-seed-addr2", amount_btc=0.5))
        s.add(TxOutput(transaction_id="entity-seed-t1", position=0, address="entity-seed-addr9", amount_btc=0.999))
    r = client.post("/api/entities/run")
    assert r.status_code == 200 and r.json()["entities"] == 1
    return client.get("/api/entities").json()["items"][0]["entity_id"]


def test_an_entity_can_be_added_to_a_case_with_its_own_evidence_snapshot(networked_client):
    eid = _seed_entity(networked_client)
    r = networked_client.post("/api/cases", json={"title": "Entity review", "items": [{"type": "entity", "id": eid}]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["item_counts"] == {"lead": 0, "wallet": 0, "transaction": 0, "cluster": 0, "entity": 1}
    assert actions(d) == ["case_created", "entity_added"]
    item = d["items"][0]
    assert item["item_type"] == "entity" and item["item_id"] == eid
    assert item["evidence_snapshot"]["kind"] == "entity" and set(item["evidence_snapshot"]["addresses"]) == {"entity-seed-addr1", "entity-seed-addr2"}
    assert item["evidence_snapshot"]["linked_wallets"] == ["wA"]
    assert item["current"] == {"exists": True, "address_count": 2, "transaction_count": 1, "total_sent_btc": 1.0, "total_received_btc": 0.0}


def test_an_unknown_entity_id_is_rejected(networked_client):
    r = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "entity", "id": "CIO-nope"}]})
    assert r.status_code == 404 and "No address entity" in r.json()["error"]["message"]


def test_the_case_graph_uses_the_entitys_linked_wallets_as_seeds(networked_client):
    eid = _seed_entity(networked_client, wallet_a="w001", wallet_b="w002")
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "entity", "id": eid}]}).json()["case_id"]
    g = networked_client.get(f"/api/cases/{case}/graph").json()
    assert {n["label"] for n in g["nodes"] if n["type"] == "wallet"} >= {"w001"}


def test_entity_item_does_not_affect_the_case_wallets_or_related_transactions_list(networked_client):
    """Matches the existing precedent for 'cluster' items: only wallet/lead items and explicit transactions count
    toward case_wallets()/related_transactions; an entity (like a cluster) only seeds the graph."""
    eid = _seed_entity(networked_client, wallet_a="w003", wallet_b="w004")
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "entity", "id": eid}]}).json()["case_id"]
    d = networked_client.get(f"/api/cases/{case}").json()
    assert d["wallets"] == []


# ---- persistence ---------------------------------------------------------------------------------------------------------------------------------
def test_cases_survive_replacing_the_synthetic_data(loaded_client, settings):
    case = loaded_client.post("/api/cases", json={"title": "keeps", "items": [{"type": "wallet", "id": "wallet_E"}, {"type": "transaction", "id": "syn-000006"}]}).json()["case_id"]
    write_raw_csv(settings.dataset_csv, SMALL_TRANSFERS[:4])                                # regenerated dataset: wallet_E and syn-000006 no longer exist
    assert loaded_client.post("/api/import/synthetic-csv", json={"replace": True}).status_code == 200
    d = loaded_client.get(f"/api/cases/{case}").json()
    assert d["title"] == "keeps" and len(d["items"]) == 2 and all(i["current"] == {"exists": False} for i in d["items"])
    assert d["items"][0]["evidence_snapshot"]["wallet"]["address"] == "wallet_E"            # the saved evidence is still there
    assert loaded_client.get(f"/api/cases/{case}/graph").status_code == 200


def test_history_and_items_are_stored_in_the_database(networked_client, networked_analysed_db):
    case = networked_client.post("/api/cases", json={"title": "c", "items": [{"type": "wallet", "id": "w001"}], "note": "n"}).json()["case_id"]
    with networked_analysed_db.session() as s:
        assert s.get(Case, case).title == "c"
        assert s.scalar(select(func.count()).select_from(CaseItem).where(CaseItem.case_id == case)) == 1
        assert [h.action for h in s.scalars(select(CaseHistory).where(CaseHistory.case_id == case).order_by(CaseHistory.entry_id))] == ["case_created", "wallet_added", "note_added"]
