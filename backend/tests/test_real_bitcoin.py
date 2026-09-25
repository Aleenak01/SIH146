"""
The optional real Bitcoin source. No test here touches the network: a fake Esplora transport with hand-built
transactions in the real Esplora JSON shape stands in for the API.
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend import cli
from backend.config import PROJECT_ROOT, Settings
from backend.ingestion.base import SourcedTransaction
from backend.ingestion.real_bitcoin import (
    EsploraClient, HttpResponse, RealBitcoinSource, SourceNotConfigured, SourceUnavailable, normalize_tx, public_url, validate_base_url,
)
from backend.main import create_app
from backend.models import NetworkObservation, Transaction, Wallet
from backend.schemas import TransactionIn
from backend.services import real_source
from backend.services.ingest import ingest

BASE = "https://esplora.test/api"
KEY = "SECRET-KEY-do-not-leak-123"
T0 = 1_760_000_000          # a confirmed block time


# ---- a fake Esplora --------------------------------------------------------------------------------------------
def txid(n: int) -> str:
    return f"{n:064x}"


def make_tx(n: int, inputs, outputs, *, block_time: int | None = T0, coinbase: bool = False) -> dict:
    """inputs: [(address, sats)], outputs: [(address | None, sats)] in the Esplora JSON shape."""
    vin = [{"txid": txid(10_000 + n), "vout": 0, "is_coinbase": True, "prevout": None}] if coinbase else [
        {"txid": txid(20_000 + n + i), "vout": 0, "is_coinbase": False, "prevout": {"scriptpubkey_address": a, "value": v}} for i, (a, v) in enumerate(inputs)]
    vout = [{"scriptpubkey_address": a, "value": v} if a else {"scriptpubkey": "6a", "scriptpubkey_type": "op_return", "value": v} for a, v in outputs]
    status = {"confirmed": True, "block_height": 800_000, "block_time": block_time} if block_time is not None else {"confirmed": False}
    return {"txid": txid(n), "vin": vin, "vout": vout, "status": status}


class FakeEsplora:
    """Callable transport. blocks: {height: [tx dicts]}. `script` is a list of (status, body) answers used first, in order."""

    def __init__(self, blocks: dict[int, list[dict]] | None = None, tip: int = 800_000) -> None:
        self.blocks, self.tip = blocks or {}, tip
        self.calls: list[tuple[str, dict]] = []
        self.script: list[HttpResponse] = []
        self.fail_paths: dict[str, int] = {}         # path substring -> status to answer

    def __call__(self, url: str, headers: dict, timeout: float) -> HttpResponse:
        self.calls.append((url, dict(headers)))
        if self.script:
            return self.script.pop(0)
        path = url.removeprefix(BASE)
        for frag, status in self.fail_paths.items():
            if frag in path:
                return HttpResponse(status, b"", {})
        if path == "/blocks/tip/height":
            return HttpResponse(200, str(self.tip).encode(), {})
        if path.startswith("/block-height/"):
            h = int(path.rsplit("/", 1)[1])
            return HttpResponse(200, f"{h:064x}".encode(), {}) if h in self.blocks else HttpResponse(404, b"Block not found", {})
        if path.startswith("/block/") and "/txs/" in path:
            block_hash, start = path.removeprefix("/block/").split("/txs/")
            txs = self.blocks[int(block_hash, 16)]
            return HttpResponse(200, json.dumps(txs[int(start): int(start) + 25]).encode(), {})
        return HttpResponse(404, b"", {})


def client_for(fake: FakeEsplora, **kw) -> EsploraClient:
    return EsploraClient(BASE, transport=fake, delay=0.0, sleep=lambda s: None, **kw)


# ---- normalization ------------------------------------------------------------------------------------------------
def test_sender_is_the_largest_input_and_change_is_dropped():
    tx = make_tx(1, inputs=[("bc1small", 10_000), ("bc1big", 90_000)], outputs=[("bc1pay", 50_000), ("bc1big", 30_000), (None, 0 + 1_000), ("bc1small", 5_000)])
    items, reason = normalize_tx(tx)
    assert reason is None and len(items) == 1
    t = items[0].transaction
    assert (t.sender_wallet, t.receiver_wallet, t.amount_btc) == ("bc1big", "bc1pay", 0.0005)
    assert (t.input_count, t.output_count) == (2, 4)                                # the real counts
    assert t.transaction_id == f"btc:{txid(1)}:0" and items[0].source_ref == f"{txid(1)}:0"
    assert t.timestamp == datetime.fromtimestamp(T0, timezone.utc).replace(tzinfo=None)


def test_one_transfer_per_receiver_with_unique_ids_and_summed_outputs():
    tx = make_tx(2, inputs=[("bc1src", 500_000)], outputs=[("bc1a", 100_000), ("bc1b", 200_000), ("bc1a", 50_000), ("bc1src", 140_000)])
    items, _ = normalize_tx(tx)
    got = {i.transaction.receiver_wallet: (i.transaction.amount_btc, i.transaction.transaction_id, i.source_ref) for i in items}
    assert got == {"bc1a": (0.0015, f"btc:{txid(2)}:0", f"{txid(2)}:0"), "bc1b": (0.002, f"btc:{txid(2)}:1", f"{txid(2)}:1")}


def test_equal_inputs_break_ties_by_address():
    items, _ = normalize_tx(make_tx(3, inputs=[("bc1zzz", 1000), ("bc1aaa", 1000)], outputs=[("bc1out", 1500)]))
    assert items[0].transaction.sender_wallet == "bc1aaa"


@pytest.mark.parametrize("tx,reason", [
    (make_tx(4, [], [("bc1x", 1000)], coinbase=True), "coinbase"),
    (make_tx(5, [("bc1a", 2000)], [("bc1b", 1000)], block_time=None), "unconfirmed"),
    (make_tx(6, [("bc1a", 2000)], [("bc1a", 1500), ("bc1a", 400)]), "no_external_output"),          # a pure consolidation / change
    (make_tx(7, [("bc1a", 2000)], [(None, 1000)]), "no_external_output"),                            # only an OP_RETURN
    ({"txid": txid(8), "status": {"confirmed": True, "block_time": T0}, "vin": [{"prevout": None, "is_coinbase": False}], "vout": [{"scriptpubkey_address": "bc1x", "value": 5}]}, "no_input_address"),
    ({"txid": "short"}, "invalid"),
])
def test_skipped_transactions_are_reported_not_invented(tx, reason):
    assert normalize_tx(tx) == ([], reason)


# ---- the client -----------------------------------------------------------------------------------------------------
def test_block_paging_and_the_per_block_cap():
    txs = [make_tx(100 + i, [(f"bc1s{i}", 5000)], [(f"bc1r{i}", 4000)]) for i in range(60)]
    fake = FakeEsplora({800_000: txs})
    everything = RealBitcoinSource(client_for(fake), blocks=1, max_tx_per_block=500)
    assert len(everything.fetch()) == 60 and everything.last_report.transactions_examined == 60
    urls = [u.removeprefix(BASE) for u, _ in fake.calls]
    assert urls[:2] == ["/blocks/tip/height", f"/block-height/800000"] and [u.rsplit("/", 1)[1] for u in urls if "/txs/" in u] == ["0", "25", "50"]

    capped = RealBitcoinSource(client_for(FakeEsplora({800_000: txs})), blocks=1, max_tx_per_block=30)
    assert len(capped.fetch()) == 30 and capped.last_report.transactions_examined == 30
    assert capped.last_report.blocks == [{"height": 800_000, "hash": f"{800_000:064x}", "transactions_examined": 30}]


def test_newest_blocks_and_explicit_heights():
    blocks = {h: [make_tx(h * 10 + i, [(f"bc1s{h}{i}", 5000)], [(f"bc1r{h}{i}", 4000)]) for i in range(2)] for h in (799_998, 799_999, 800_000)}
    src = RealBitcoinSource(client_for(FakeEsplora(blocks)), blocks=2)
    src.fetch()
    assert [b["height"] for b in src.last_report.blocks] == [800_000, 799_999]
    src = RealBitcoinSource(client_for(FakeEsplora(blocks)), heights=[799_998])
    src.fetch()
    assert [b["height"] for b in src.last_report.blocks] == [799_998]
    with pytest.raises(ValueError):
        RealBitcoinSource(client_for(FakeEsplora(blocks)), blocks=6, max_blocks=5)
    with pytest.raises(ValueError):
        RealBitcoinSource(client_for(FakeEsplora(blocks)), blocks=0)


def test_retries_rate_limits_then_succeeds_and_honours_retry_after():
    fake = FakeEsplora()
    fake.script = [HttpResponse(429, b"", {"retry-after": "3"}), HttpResponse(503, b"", {}), HttpResponse(200, b"800123", {})]
    waits: list[float] = []
    c = EsploraClient(BASE, transport=fake, delay=0.0, sleep=waits.append)
    assert c.tip_height() == 800_123 and waits == [3.0, 2.0]


def test_gives_up_with_a_clear_error_and_never_returns_partial_data():
    fake = FakeEsplora()
    fake.script = [HttpResponse(429, b"", {})] * 10
    with pytest.raises(SourceUnavailable, match="rate limiting"):
        client_for(fake).tip_height()
    for body, status, match in [(b"nope", 200, "unexpected chain height"), (b"", 404, "no such resource"), (b"", 418, "HTTP 418")]:
        f = FakeEsplora()
        f.script = [HttpResponse(status, body, {})]
        with pytest.raises(SourceUnavailable, match=match):
            client_for(f).tip_height()
    f = FakeEsplora({800_000: []})
    f.script = [HttpResponse(200, b"800000", {}), HttpResponse(200, b"not-a-hash", {})]
    with pytest.raises(SourceUnavailable, match="unexpected block hash"):
        RealBitcoinSource(client_for(f), blocks=1).fetch()
    f = FakeEsplora({800_000: []})
    f.script = [HttpResponse(200, b"800000", {}), HttpResponse(200, f"{800_000:064x}".encode(), {}), HttpResponse(200, b"<html>", {})]
    with pytest.raises(SourceUnavailable, match="not valid JSON"):
        RealBitcoinSource(client_for(f), blocks=1).fetch()


def test_transport_errors_become_source_unavailable():
    attempts, waits = [], []

    def down(url, headers, timeout):
        attempts.append(url)
        raise SourceUnavailable("Could not reach the Bitcoin data source (URLError).")

    with pytest.raises(SourceUnavailable, match="Could not reach"):
        EsploraClient(BASE, transport=down, delay=0.0, sleep=waits.append).tip_height()
    assert len(attempts) == 4 and waits == [1.0, 2.0, 4.0]                          # three retries with backoff, then a clear error

    flaky = {"n": 0}

    def once(url, headers, timeout):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise SourceUnavailable("Could not reach the Bitcoin data source (TimeoutError).")
        return HttpResponse(200, b"800000", {})

    assert EsploraClient(BASE, transport=once, delay=0.0, sleep=lambda s: None).tip_height() == 800_000


@pytest.mark.parametrize("url", ["ftp://x.test/api", "file:///etc/passwd", "not a url", "", "https://user:pw@x.test/api"])
def test_unsafe_or_broken_base_urls_are_refused(url):
    with pytest.raises(SourceNotConfigured):
        validate_base_url(url)


def test_public_url_hides_credentials_and_query():
    assert public_url("https://user:pw@x.test:8443/api/?apikey=SECRET#f") == "https://x.test:8443/api"
    assert validate_base_url("https://esplora.test/api/") == "https://esplora.test/api"


def test_the_api_key_is_sent_only_as_a_bearer_header_and_only_when_set():
    fake = FakeEsplora()
    client_for(fake).tip_height()
    client_for(fake, api_key=KEY).tip_height()
    assert "Authorization" not in fake.calls[0][1] and fake.calls[1][1]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in fake.calls[1][0]                                    # never in the URL
    s = Settings(db_path=PROJECT_ROOT / "x.db", dataset_csv=PROJECT_ROOT / "x.csv", host="h", port=1, real_bitcoin_api_key=KEY)
    assert KEY not in repr(s)


# ---- an enabled server ------------------------------------------------------------------------------------------------
@pytest.fixture
def fake() -> FakeEsplora:
    txs = [make_tx(1000 + i, [(f"bc1sender{i % 7}", 900_000), (f"bc1other{i % 3}", 10_000)], [(f"bc1recv{i % 11}", 400_000), (f"bc1sender{i % 7}", 490_000)],
                   block_time=T0 + i * 60) for i in range(40)]
    txs.insert(0, make_tx(999, [], [("bc1miner", 625_000_000)], coinbase=True))
    txs.insert(5, make_tx(998, [("bc1sender0", 5000)], [("bc1sender0", 4000)]))
    return FakeEsplora({800_000: txs})


@pytest.fixture
def real_client(settings, fake) -> TestClient:
    enabled = dataclasses.replace(settings, real_bitcoin_enabled=True, real_bitcoin_base_url=BASE, real_bitcoin_api_key=KEY, real_bitcoin_request_delay=0.0)
    app = create_app(enabled)
    app.state.real_bitcoin_transport = fake
    with TestClient(app) as c:
        yield c


def test_disabled_by_default_and_a_fetch_never_touches_the_network(client, settings, monkeypatch):
    calls = []
    monkeypatch.setattr("backend.routers.sources.urllib_transport", lambda *a: calls.append(a))
    body = client.get("/api/sources/real-bitcoin").json()
    assert body["status"] == "not_configured" and body["enabled"] is False and body["label"] == "Adapter ready; external source not configured"
    r = client.post("/api/sources/real-bitcoin/fetch", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "source_not_enabled" and "SIH146_REAL_BITCOIN_ENABLED" in r.json()["error"]["message"]
    assert calls == []
    assert client.get("/api/overview").json()["by_source"] == {}                                # nothing was stored


def test_source_listing_and_settings_report_the_state_without_secrets(real_client, client):
    for c, status in ((client, "not_configured"), (real_client, "configured")):
        listing = c.get("/api/sources").json()["sources"]
        assert [(s["source"], s["is_real_data"]) for s in listing] == [("synthetic", False), ("synthetic", False), ("real_bitcoin", True)]
        assert listing[2]["status"] == status
        assert c.get("/api/settings").json()["data_source"]["real_bitcoin"]["status"] == status
        assert c.get("/api/settings").json()["data_source"]["is_real_data"] is False       # what is being monitored is still synthetic
    detail = real_client.get("/api/sources/real-bitcoin").json()
    assert detail["api_key_configured"] is True and detail["base_url"] == BASE
    assert KEY not in json.dumps([real_client.get(p).json() for p in ("/api/sources", "/api/sources/real-bitcoin", "/api/settings", "/api/health", "/api/overview")])


def test_feature_availability_is_stated_and_matches_the_real_feature_code():
    from backend.analysis import ml_bridge as mb
    from backend.models import FEATURE_COLUMNS

    listed = {f["feature"]: f["availability"] for f in real_source.feature_availability()}
    assert list(listed) == list(FEATURE_COLUMNS) and len(listed) == 18
    assert {k for k, v in listed.items() if v == "needs_two_transactions"} == set(real_source.NEEDS_TWO_TRANSACTIONS)

    # verify against the actual pipeline: a wallet with a single transaction has exactly those features missing
    fe = mb.load_ml()[0]
    t = datetime(2026, 1, 1)
    features = fe.build_wallet_features(mb.build_raw_frame([(t, "a", "b", 1.0, 1, 1), (t + timedelta(hours=1), "a", "c", 0.5, 1, 1)])).round(6)
    single = features[features["transaction_count"] == 1].iloc[0]
    assert {c for c in FEATURE_COLUMNS if single[c] != single[c]} == set(real_source.NEEDS_TWO_TRANSACTIONS)


def test_a_fetch_stores_real_transfers_separately(real_client, fake):
    r = real_client.post("/api/sources/real-bitcoin/fetch", json={"blocks": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["transactions_examined"] == 42 and out["transfers"] == 40 and out["inserted"] == 40 and out["duplicates"] == 0
    assert out["skipped"] == {"coinbase": 1, "no_external_output": 1} and out["blocks"][0]["height"] == 800_000
    assert KEY not in json.dumps(out)
    assert all(h.get("Authorization") == f"Bearer {KEY}" for _, h in fake.calls)

    ov = real_client.get("/api/overview").json()
    assert ov["by_source"]["real_bitcoin"]["transactions"] == 40 and "synthetic" not in ov["by_source"]
    rows = real_client.get("/api/transactions", params={"source": "real_bitcoin", "limit": 500}).json()
    assert rows["total"] == 40 and all(t["source"] == "real_bitcoin" and t["transaction_id"].startswith("btc:") for t in rows["items"])
    sample = rows["items"][0]
    assert sample["input_count"] == 2 and sample["output_count"] == 2 and sample["sender_wallet"].startswith("bc1sender")

    detail = real_client.get("/api/sources/real-bitcoin").json()
    assert detail["stored"]["transactions"] == 40 and detail["stored"]["wallets"] > 10 and detail["last_fetch"]["inserted"] == 40


def test_real_transactions_never_get_synthetic_network_observations(real_client):
    real_client.post("/api/sources/real-bitcoin/fetch", json={})
    db = real_client.app.state.db
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) == 0
        assert s.scalar(select(func.count()).select_from(Wallet).where(Wallet.source == "real_bitcoin")) > 0
    assert real_client.get("/api/network/observations").json()["total"] == 0
    detail = real_client.get("/api/sources/real-bitcoin").json()
    assert set(detail["network_metadata"]) == {"ip_address", "device", "session"} and all(v.startswith("Not available") for v in detail["network_metadata"].values())


def test_refetching_adds_nothing_twice(real_client):
    first = real_client.post("/api/sources/real-bitcoin/fetch", json={}).json()
    second = real_client.post("/api/sources/real-bitcoin/fetch", json={}).json()
    assert (second["inserted"], second["duplicates"]) == (0, first["inserted"])
    assert real_client.get("/api/overview").json()["by_source"]["real_bitcoin"]["transactions"] == first["inserted"]


def test_an_api_failure_stores_nothing_and_invents_nothing(real_client, fake):
    fake.fail_paths["/txs/25"] = 404                                # the second page of the block fails after the first succeeded
    r = real_client.post("/api/sources/real-bitcoin/fetch", json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "source_unavailable"
    assert real_client.get("/api/overview").json()["by_source"] == {}
    assert "no such resource" in real_client.get("/api/sources/real-bitcoin").json()["last_error"]


def test_request_limits_are_enforced(real_client):
    assert real_client.post("/api/sources/real-bitcoin/fetch", json={"blocks": 6}).json()["error"]["code"] == "invalid_request"      # server cap is 5
    assert real_client.post("/api/sources/real-bitcoin/fetch", json={"blocks": 0}).status_code == 422
    assert real_client.post("/api/sources/real-bitcoin/fetch", json={"password": "x"}).status_code == 422
    r = real_client.post("/api/sources/real-bitcoin/fetch", json={"heights": [123]})            # a height the API does not have
    assert r.status_code == 502 and r.json()["error"]["code"] == "source_unavailable"


def test_a_real_address_cannot_be_mixed_into_the_synthetic_data(db):
    with db.transaction() as s:
        ingest(s, [SourcedTransaction(TransactionIn(timestamp=datetime(2026, 1, 1), sender_wallet="bc1shared", receiver_wallet="wallet_1", amount_btc=1.0))], "synthetic", observe=False)
        out = ingest(s, [SourcedTransaction(TransactionIn(transaction_id="btc:x:0", timestamp=datetime(2026, 1, 2), sender_wallet="bc1shared", receiver_wallet="bc1new", amount_btc=1.0), "x:0")], "real_bitcoin")
    assert out.inserted == 0 and "different data source" in out.rejected[0]["error"]


# ---- analysis of real data ---------------------------------------------------------------------------------------------
def busy_chain(n_wallets: int = 30, n_tx: int = 220, seed: int = 5) -> FakeEsplora:
    rnd = random.Random(seed)
    txs = []
    for i in range(n_tx):
        a, b = rnd.sample(range(n_wallets), 2)
        txs.append(make_tx(5000 + i, [(f"bc1w{a:02d}", 2_000_000)], [(f"bc1w{b:02d}", rnd.randint(10_000, 1_500_000)), (f"bc1w{a:02d}", 100_000)], block_time=T0 + i * 600))
    return FakeEsplora({800_000: txs})


def test_too_little_real_data_is_reported_not_faked(real_client):
    real_client.post("/api/sources/real-bitcoin/fetch", json={})             # 40 transfers, mostly wallets with one transaction
    r = real_client.post("/api/analysis/run", params={"source": "real_bitcoin"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "analysis_unavailable" and "at least 20" in r.json()["error"]["message"]
    assert real_client.post("/api/analysis/run", params={"source": "made_up"}).status_code == 422


def test_real_data_is_analysed_on_its_own_and_stays_separate(settings):
    enabled = dataclasses.replace(settings, real_bitcoin_enabled=True, real_bitcoin_base_url=BASE, real_bitcoin_request_delay=0.0, real_bitcoin_max_tx_per_block=500)
    app = create_app(enabled)
    app.state.real_bitcoin_transport = busy_chain()
    with TestClient(app) as c:
        c.post("/api/import/synthetic-csv", json={})                                      # a few synthetic transactions exist too
        synthetic_before = c.get("/api/overview").json()["by_source"]["synthetic"]
        assert c.post("/api/sources/real-bitcoin/fetch", json={}).json()["inserted"] == 220
        detail = c.get("/api/sources/real-bitcoin").json()["stored"]
        assert detail["wallets"] == 30 and detail["wallets_with_two_or_more_transactions"] >= 25

        run = c.post("/api/analysis/run", params={"source": "real_bitcoin"})
        assert run.status_code == 200, run.text
        s = run.json()
        assert s["source"] == "real_bitcoin" and s["transfer_count"] == 220 and s["scored_wallets"] >= 25 and s["clusters_total"] >= 1

        leads = c.get("/api/leads", params={"source": "real_bitcoin", "limit": 100}).json()
        assert leads["total"] >= 1 and all(l["source"] == "real_bitcoin" and l["wallet_address"].startswith("bc1w") for l in leads["items"])
        wallet = leads["items"][0]["wallet_address"]
        a = c.get(f"/api/wallets/{wallet}/analysis").json()
        assert a["scored"] is True and a["run_id"] == s["run_id"] and len(a["features"]) == 18

        # nothing synthetic changed, and no synthetic network data appeared for real wallets
        assert c.get("/api/overview").json()["by_source"]["synthetic"] == synthetic_before
        assert c.get("/api/analysis/wallets").json()["run_id"] is None                     # the synthetic bulk view has no real wallets in it
        assert c.get("/api/network/observations", params={"wallet": wallet}).json()["total"] == 0
        clusters = c.get("/api/clusters", params={"source": "real_bitcoin"}).json()["items"]
        assert clusters and all(x["method"] == "transaction_community" for x in clusters)      # no shared-observation clusters without observations
        assert c.get("/api/overview").json()["clusters_total"] == 0                        # the synthetic overview does not count real clusters


# ---- command line ------------------------------------------------------------------------------------------------------
def test_cli_fetch_is_refused_when_not_enabled(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIH146_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.delenv("SIH146_REAL_BITCOIN_ENABLED", raising=False)
    monkeypatch.setattr(sys, "argv", ["cli"])
    assert not (PROJECT_ROOT / ".env").is_file() or "SIH146_REAL_BITCOIN_ENABLED=true" not in (PROJECT_ROOT / ".env").read_text()
    assert cli.main(["fetch-real", "--blocks", "1"]) == 1
    assert "not enabled" in capsys.readouterr().err
