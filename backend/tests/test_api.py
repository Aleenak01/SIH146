"""HTTP API: transactions, wallets, import, errors."""

from __future__ import annotations

import pytest

from .conftest import SMALL_TRANSFERS, write_raw_csv


def new_tx(**kw):
    return {"sender_wallet": "wallet_X", "receiver_wallet": "wallet_Y", "timestamp": "2026-01-01T10:00:00Z", "amount_btc": 0.4, **kw}


# ---- system -------------------------------------------------------------------------------------
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "path" not in r.text.lower()


def test_unknown_route_and_method_use_the_error_shape(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.delete("/api/health").json()["error"]["code"] == "method_not_allowed"


def test_overview_counts_by_source(loaded_client):
    body = loaded_client.get("/api/overview").json()
    assert (body["transactions_total"], body["wallets_total"]) == (6, 5)
    assert body["by_source"] == {"synthetic": {"transactions": 6, "wallets": 5}}
    assert body["first_transaction_at"] == "2025-10-01T08:00:00Z" and body["last_transaction_at"] == "2026-09-24T21:00:00Z"


def test_overview_on_an_empty_database(client):
    body = client.get("/api/overview").json()
    assert body["transactions_total"] == 0 and body["first_transaction_at"] is None


# ---- CSV import endpoint ------------------------------------------------------------------------
def test_import_endpoint_and_idempotency(client):
    a = client.post("/api/import/synthetic-csv", json={}).json()
    b = client.post("/api/import/synthetic-csv").json()          # no body at all is fine
    assert (a["inserted"], a["wallets_total"]) == (6, 5) and (b["inserted"], b["skipped_existing"]) == (0, 6)


def test_import_endpoint_conflict_and_replace(loaded_client, settings):
    changed = list(SMALL_TRANSFERS)
    changed[1] = ("2025-10-02 09:30:00", "wallet_B", "wallet_C", 0.99, 2, 1)
    write_raw_csv(settings.dataset_csv, changed)
    r = loaded_client.post("/api/import/synthetic-csv", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "import_conflict"
    assert loaded_client.post("/api/import/synthetic-csv", json={"replace": True}).status_code == 200
    assert loaded_client.get("/api/transactions/syn-000002").json()["amount_btc"] == 0.99


def test_import_endpoint_reports_a_bad_dataset(client, settings):
    write_raw_csv(settings.dataset_csv, mirror=False)
    r = client.post("/api/import/synthetic-csv", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_dataset"
    assert client.post("/api/import/synthetic-csv", json={"replace": "maybe", "x": 1}).status_code == 422


# ---- transactions: reading ----------------------------------------------------------------------
def test_list_pagination_and_ordering(loaded_client):
    page = loaded_client.get("/api/transactions", params={"limit": 4, "offset": 0}).json()
    assert page["total"] == 6 and len(page["items"]) == 4
    assert page["items"][0]["transaction_id"] == "syn-000006"                     # newest first by default
    rest = loaded_client.get("/api/transactions", params={"limit": 4, "offset": 4}).json()
    assert [t["transaction_id"] for t in rest["items"]] == ["syn-000002", "syn-000001"] and rest["total"] == 6
    asc = loaded_client.get("/api/transactions", params={"order": "asc", "limit": 1}).json()["items"][0]
    assert asc["transaction_id"] == "syn-000001" and asc["timestamp"] == "2025-10-01T08:00:00Z"


@pytest.mark.parametrize("params,expected_ids", [
    ({"wallet": "wallet_A"}, {"syn-000001", "syn-000003", "syn-000005", "syn-000006"}),
    ({"sender": "wallet_C"}, {"syn-000004"}),
    ({"receiver": "wallet_C"}, {"syn-000002", "syn-000003"}),
    ({"min_amount": 1.0}, {"syn-000003", "syn-000005"}),
    ({"max_amount": 0.1}, {"syn-000004", "syn-000006"}),
    ({"start": "2025-11-01T00:00:00", "end": "2026-03-03T18:45:10"}, {"syn-000004", "syn-000005"}),
    ({"q": "syn-000003"}, {"syn-000003"}),
    ({"q": "wallet_E"}, {"syn-000006"}),
    ({"source": "synthetic", "wallet": "wallet_D"}, {"syn-000004", "syn-000005"}),
    ({"source": "real_bitcoin"}, set()),
])
def test_transaction_filters(loaded_client, params, expected_ids):
    body = loaded_client.get("/api/transactions", params=params).json()
    assert {t["transaction_id"] for t in body["items"]} == expected_ids and body["total"] == len(expected_ids)


def test_search_text_is_matched_literally(loaded_client):
    assert loaded_client.get("/api/transactions", params={"q": "%"}).json()["total"] == 0
    assert loaded_client.get("/api/transactions", params={"q": "_"}).json()["total"] == 6      # '_' occurs in every wallet id, as a literal
    assert loaded_client.get("/api/transactions", params={"q": "'; DROP TABLE transactions;--"}).json()["total"] == 0
    assert loaded_client.get("/api/overview").json()["transactions_total"] == 6


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"sort": "password"}, {"order": "sideways"}, {"source": "mainnet"}, {"start": "yesterday"}])
def test_bad_query_parameters_are_422(loaded_client, params):
    r = loaded_client.get("/api/transactions", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_get_transaction(loaded_client):
    t = loaded_client.get("/api/transactions/syn-000004").json()
    assert (t["sender_wallet"], t["receiver_wallet"], t["source"], t["source_ref"]) == ("wallet_C", "wallet_D", "synthetic", "row:8")
    assert loaded_client.get("/api/transactions/syn-999999").status_code == 404


# ---- transactions: writing ----------------------------------------------------------------------
def test_post_transaction_creates_transaction_and_wallets(loaded_client):
    r = loaded_client.post("/api/transactions", json=new_tx())
    assert r.status_code == 201
    body = r.json()
    assert body["transaction_id"] == "syn-000007" and body["source"] == "synthetic" and body["source_ref"] is None
    x = loaded_client.get("/api/wallets/wallet_X").json()
    assert (x["transaction_count"], x["outgoing_count"], x["first_seen"]) == (1, 1, "2026-01-01T10:00:00Z")
    assert loaded_client.get("/api/overview").json()["transactions_total"] == 7


def test_post_updates_an_existing_wallet(loaded_client):
    before = loaded_client.get("/api/wallets/wallet_A").json()
    assert before["last_seen"] == "2026-09-24T21:00:00Z"
    # an in-between transaction changes the counts but not the seen-range
    loaded_client.post("/api/transactions", json=new_tx(sender_wallet="wallet_A", timestamp="2026-05-05T00:00:00Z", amount_btc=1.5))
    middle = loaded_client.get("/api/wallets/wallet_A").json()
    assert middle["transaction_count"] == before["transaction_count"] + 1
    assert (middle["first_seen"], middle["last_seen"]) == (before["first_seen"], before["last_seen"])
    assert middle["total_sent_btc"] == pytest.approx(before["total_sent_btc"] + 1.5)
    # later and earlier transactions extend the range
    loaded_client.post("/api/transactions", json=new_tx(sender_wallet="wallet_A", timestamp="2026-09-25T09:00:00Z"))
    loaded_client.post("/api/transactions", json=new_tx(sender_wallet="wallet_A", timestamp="2025-09-01T00:00:00Z"))
    after = loaded_client.get("/api/wallets/wallet_A").json()
    assert (after["first_seen"], after["last_seen"]) == ("2025-09-01T00:00:00Z", "2026-09-25T09:00:00Z")
    assert loaded_client.get("/api/wallets/wallet_A").json()["transaction_count"] == before["transaction_count"] + 3


def test_post_duplicate_id_is_409(client):
    assert client.post("/api/transactions", json=new_tx(transaction_id="syn-000050")).status_code == 201
    r = client.post("/api/transactions", json=new_tx(transaction_id="syn-000050"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_transaction"
    r = client.post("/api/transactions", json=new_tx(transaction_id="syn-000050", amount_btc=9))
    assert r.status_code == 409 and "different content" in r.json()["error"]["message"]


@pytest.mark.parametrize("bad", [
    {"amount_btc": -1}, {"amount_btc": 0}, {"receiver_wallet": "wallet_X"}, {"timestamp": "soon"},
    {"sender_wallet": "bad wallet!"}, {"source": "real_bitcoin"}, {"unexpected": 1}, {"amount_btc": "lots"},
])
def test_post_invalid_transaction_is_422_and_stores_nothing(client, bad):
    r = client.post("/api/transactions", json=new_tx(**bad))
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]
    assert client.get("/api/overview").json()["transactions_total"] == 0


def test_post_empty_and_non_json_bodies(client):
    assert client.post("/api/transactions", json={}).status_code == 422
    assert client.post("/api/transactions", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422


def test_batch_saves_valid_items_and_reports_the_rest_by_index(client):
    payload = [new_tx(), {"amount_btc": -2}, new_tx(sender_wallet="wallet_P", receiver_wallet="wallet_Q"), "junk", new_tx(timestamp="nope")]
    r = client.post("/api/transactions/batch", json=payload)
    body = r.json()
    assert r.status_code == 200 and body["inserted"] == 2
    assert [x["index"] for x in body["rejected"]] == [1, 3, 4]
    assert client.get("/api/overview").json()["transactions_total"] == 2


def test_batch_rejects_oversized_requests(client):
    assert client.post("/api/transactions/batch", json=[new_tx()] * 5001).status_code == 422


# ---- wallets --------------------------------------------------------------------------------------
def test_wallet_list_detail_and_sorting(loaded_client):
    lst = loaded_client.get("/api/wallets").json()
    assert lst["total"] == 5 and [w["address"] for w in lst["items"]] == ["wallet_A", "wallet_B", "wallet_C", "wallet_D", "wallet_E"]
    a = lst["items"][0]
    assert (a["incoming_count"], a["outgoing_count"], a["transaction_count"]) == (2, 2, 4)
    assert a["total_received_btc"] == pytest.approx(2.05) and a["total_sent_btc"] == pytest.approx(1.5)

    top = loaded_client.get("/api/wallets", params={"sort": "total_received_btc", "order": "desc", "limit": 1}).json()["items"][0]
    assert top["address"] == "wallet_A"
    assert loaded_client.get("/api/wallets", params={"q": "wallet_E"}).json()["total"] == 1
    assert loaded_client.get("/api/wallets", params={"sort": "nonsense"}).status_code == 422


def test_wallet_detail_counterparties(loaded_client):
    a = loaded_client.get("/api/wallets/wallet_A").json()
    assert a["unique_counterparties"] == 4          # B, C, D, E
    assert a["source"] == "synthetic" and a["first_seen"] == "2025-10-01T08:00:00Z" and a["last_seen"] == "2026-09-24T21:00:00Z"
    r = loaded_client.get("/api/wallets/nobody")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_wallet_transactions(loaded_client):
    body = loaded_client.get("/api/wallets/wallet_C/transactions", params={"order": "asc"}).json()
    assert body["total"] == 3 and [t["transaction_id"] for t in body["items"]] == ["syn-000002", "syn-000003", "syn-000004"]
    assert loaded_client.get("/api/wallets/nobody/transactions").status_code == 404


def test_unexpected_server_errors_do_not_leak_details(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.routers import health

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail /home/user/db")

    app = create_app(settings)
    monkeypatch.setattr(health, "select", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/health")
    assert r.status_code == 500 and r.json() == {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
    assert "secret" not in r.text
