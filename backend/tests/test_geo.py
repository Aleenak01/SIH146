"""
Tests for Phase 4 Part A's geo aggregation (backend/services/geo_queries.py, /api/wallets/{id}/geo, /api/geo/summary).
Built on flow_records.geo_country/asn/asn_org -- synthetic GeoIP demo data, never observed traffic. All data here
is synthetic and crafted for these tests; no test needs the real GeoIP files.
"""

from __future__ import annotations

from datetime import datetime

from backend.models import FlowRecord, Transaction, Wallet
from backend.services.geo_queries import geo_summary, wallet_geo

T0 = datetime(2026, 1, 1)


def _mk_wallets(s, *wallets: str) -> None:
    for w in wallets:
        if s.get(Wallet, w) is None:
            s.add(Wallet(address=w, source="synthetic"))
    s.flush()


def test_unknown_wallet_returns_none(db):
    with db.session() as s:
        assert wallet_geo(s, "nope") is None


def test_a_wallet_with_no_flow_records_has_empty_breakdown(db):
    with db.transaction() as s:
        _mk_wallets(s, "w1")
    with db.session() as s:
        out = wallet_geo(s, "w1")
    assert out == {"wallet_address": "w1", "transaction_count": 0, "countries": [], "asns": [], "note": out["note"]}


def test_wallet_geo_aggregates_country_and_asn_counts(db):
    with db.transaction() as s:
        _mk_wallets(s, "w1", "w2")
        for i, (country, asn, org) in enumerate([("US", 100, "OrgA"), ("US", 100, "OrgA"), ("DE", 200, "OrgB")]):
            tid = f"t{i}"
            s.add(Transaction(transaction_id=tid, timestamp=T0, sender_wallet="w1", receiver_wallet="w2", amount_btc=1.0, input_count=1, output_count=1, source="synthetic"))
            s.flush()
            s.add(FlowRecord(transaction_id=tid, src_ip="203.0.113.1", dst_ip="203.0.113.2", geo_country=country, asn=asn, asn_org=org))
    with db.session() as s:
        out = wallet_geo(s, "w1")
    assert out["transaction_count"] == 3
    assert out["countries"] == [{"country": "US", "count": 2}, {"country": "DE", "count": 1}]
    assert out["asns"] == [{"asn": 100, "asn_org": "OrgA", "count": 2}, {"asn": 200, "asn_org": "OrgB", "count": 1}]
    with db.session() as s:
        out2 = wallet_geo(s, "w2")
    assert {k: v for k, v in out2.items() if k != "wallet_address"} == {k: v for k, v in out.items() if k != "wallet_address"}  # receiver sees the same traffic


def test_geo_summary_scopes_to_lead_wallets_only(analysed_client):
    """w000 is analysed_db's hub wallet and is reliably a lead (see conftest.seed_population); w030 is checked
    empirically (test_confidence.py) to never be one for this fixed seed. Uses the real leads pipeline
    (InvestigativeLead), computed by the analysed_db fixture, rather than hand-inserting one, since that table's
    contents are a computed/derived state, not a plain fact to seed directly (see leads.py)."""
    db = analysed_client.app.state.db
    with db.transaction() as s:
        from sqlalchemy import select

        from backend.models import InvestigativeLead

        lead_tx = s.scalar(select(Transaction.transaction_id).where((Transaction.sender_wallet == "w000") | (Transaction.receiver_wallet == "w000")))
        non_lead_tx = s.scalar(select(Transaction.transaction_id).where((Transaction.sender_wallet == "w030") | (Transaction.receiver_wallet == "w030")))
        assert lead_tx and non_lead_tx
        s.add(FlowRecord(transaction_id=lead_tx, geo_country="US", asn=100, asn_org="OrgA"))
        s.add(FlowRecord(transaction_id=non_lead_tx, geo_country="DE", asn=200, asn_org="OrgB"))
        leads = set(s.scalars(select(InvestigativeLead.wallet_address)))
        assert "w000" in leads and "w030" not in leads

    with db.session() as s:
        out = geo_summary(s, source="synthetic")
    assert out["lead_count"] == len(leads)
    countries = {c["country"] for c in out["top_countries"]}
    assert "US" in countries and "DE" not in countries


def test_wallet_geo_endpoint(client):
    db = client.app.state.db
    with db.transaction() as s:
        _mk_wallets(s, "w1")
    r = client.get("/api/wallets/w1/geo")
    assert r.status_code == 200
    assert r.json() == {"wallet_address": "w1", "transaction_count": 0, "countries": [], "asns": [], "note": r.json()["note"]}
    assert "not observed network traffic" in r.json()["note"]


def test_wallet_geo_endpoint_404_for_unknown_wallet(client):
    assert client.get("/api/wallets/nope/geo").status_code == 404


def test_geo_summary_endpoint(client):
    r = client.get("/api/geo/summary")
    assert r.status_code == 200
    body = r.json()
    assert body == {"source": "synthetic", "lead_count": 0, "top_countries": [], "top_asns": [], "note": body["note"]}
