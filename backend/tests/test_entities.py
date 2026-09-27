"""
Tests for Phase 2: common-input-ownership entities, network correlation, and their API
(backend/analysis/entities.py, backend/analysis/correlation.py, backend/routers/entities.py). All data here is
synthetic and crafted for these tests; no test needs the internet or the real GeoIP MMDB files.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.analysis.correlation import refresh_correlation
from backend.analysis.entities import assign_stable_ids, compute_common_input_entities, refresh_entities
from backend.models import (
    AddressEntity, AddressEntityMember, CorrelationFinding, EntityLink, FlowRecord, InvestigativeLead, Transaction, TxDetails, TxInput, TxOutput, Wallet,
)

FORBIDDEN = {"sender_wallet", "receiver_wallet", "ground_truth", "ground_truth_address_owner"}
# amount_btc is deliberately NOT forbidden here: it is also the legitimate per-row column name on TxInput/TxOutput
# (the address-level amounts entities.py must use). The actual leak field is Transaction.amount_btc / the rich
# record's single flat-view amount, which neither module ever references (checked via sender_wallet/receiver_wallet,
# which always accompany it in the flat view and are never needed for address-level analysis).


# ---------------------------------------------------------------------------------------------------------------
# 1. Pure union-find computation
# ---------------------------------------------------------------------------------------------------------------
def test_chain_of_transactions_merges_into_one_entity():
    # tx1: a+b spent together; tx2: b+c spent together -> a,b,c end up in one entity (a chain, not a direct pair)
    groups = compute_common_input_entities([("tx1", "a"), ("tx1", "b"), ("tx2", "b"), ("tx2", "c")])
    assert len(groups) == 1
    assert groups[0].addresses == ["a", "b", "c"]
    assert groups[0].transaction_ids == {"tx1", "tx2"}


def test_disjoint_transactions_stay_separate():
    groups = compute_common_input_entities([("tx1", "a"), ("tx1", "b"), ("tx2", "c"), ("tx2", "d")])
    assert sorted(g.addresses for g in groups) == [["a", "b"], ["c", "d"]]


def test_a_cycle_of_shared_transactions_still_merges_into_one_entity():
    # a-b in tx1, b-c in tx2, c-a in tx3: must still resolve to one entity with no duplication or infinite loop
    groups = compute_common_input_entities([("tx1", "a"), ("tx1", "b"), ("tx2", "b"), ("tx2", "c"), ("tx3", "c"), ("tx3", "a")])
    assert len(groups) == 1
    assert groups[0].addresses == ["a", "b", "c"]


def test_single_input_address_is_its_own_entity():
    groups = compute_common_input_entities([("tx1", "solo")])
    assert len(groups) == 1
    assert groups[0].addresses == ["solo"]


def test_default_id_is_the_lowest_address():
    groups = compute_common_input_entities([("tx1", "zzz"), ("tx1", "aaa")])
    assert groups[0].entity_id == "CIO-aaa"


# ---------------------------------------------------------------------------------------------------------------
# 2. Determinism and stable ids
# ---------------------------------------------------------------------------------------------------------------
def test_recomputing_identical_input_gives_identical_ids():
    inputs = [("tx1", "b"), ("tx1", "a"), ("tx2", "c"), ("tx2", "d")]
    g1 = compute_common_input_entities(inputs)
    g2 = compute_common_input_entities(list(inputs))
    assert [g.entity_id for g in g1] == [g.entity_id for g in g2]


def test_an_entity_keeps_its_old_id_after_a_new_transaction_only_adds_addresses():
    existing = {"CIO-m": {"m", "n"}}
    computed = compute_common_input_entities([("tx1", "m"), ("tx1", "n"), ("tx2", "n"), ("tx2", "z")])
    assign_stable_ids(computed, existing)
    assert computed[0].entity_id == "CIO-m"


def test_two_existing_entities_merging_keeps_the_larger_overlaps_id():
    existing = {"CIO-a": {"a", "b"}, "CIO-x": {"x"}}
    computed = compute_common_input_entities([("tx1", "a"), ("tx1", "b"), ("tx1", "x")])   # a,b,x now merge in one tx
    assign_stable_ids(computed, existing)
    assert computed[0].entity_id == "CIO-a"    # CIO-a had overlap 2 (a,b), CIO-x had overlap 1 (x): CIO-a wins


def test_a_brand_new_group_with_no_overlap_gets_a_fresh_id():
    existing = {"CIO-a": {"a", "b"}}
    computed = compute_common_input_entities([("tx1", "q"), ("tx1", "r")])
    assign_stable_ids(computed, existing)
    assert computed[0].entity_id == "CIO-q"


# ---------------------------------------------------------------------------------------------------------------
# 3. No leaked fields: these modules must never reference the flat-view or ground-truth identifiers
# ---------------------------------------------------------------------------------------------------------------
def _referenced_identifiers(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return names | attrs | strings


def test_entities_module_never_references_leak_fields_or_ground_truth():
    import backend.analysis.entities as mod
    assert not (FORBIDDEN & _referenced_identifiers(mod))


def test_correlation_module_never_references_leak_fields_or_ground_truth():
    import backend.analysis.correlation as mod
    assert not (FORBIDDEN & _referenced_identifiers(mod))


# ---------------------------------------------------------------------------------------------------------------
# 4. Persistence: refresh_entities / refresh_correlation on a small crafted database
# ---------------------------------------------------------------------------------------------------------------
def _mk_wallets(s, *wallets: str) -> None:
    for w in wallets:
        if s.get(Wallet, w) is None:
            s.add(Wallet(address=w, source="synthetic"))
    s.flush()


def _seed_rich_tx(db, *, tx_id: str, wallet_a: str, wallet_b: str, input_addrs: list[str], output_addrs: list[str],
                  ts: datetime, src_ip: str | None = None, dst_port: int = 8333, country: str | None = None, asn: int | None = None) -> None:
    """One flat transaction wallet_a -> wallet_b, plus its rich address-level detail (inputs/outputs/flow)."""
    with db.transaction() as s:
        _mk_wallets(s, wallet_a, wallet_b)
        s.add(Transaction(transaction_id=tx_id, timestamp=ts, sender_wallet=wallet_a, receiver_wallet=wallet_b,
                          amount_btc=1.0, input_count=len(input_addrs), output_count=len(output_addrs), source="synthetic"))
        s.flush()   # the detail rows below have a plain FK column (no relationship()); flush so Transaction exists first
        txid = format(abs(hash(tx_id)) % (16 ** 64), "064x")[:64]
        s.add(TxDetails(transaction_id=tx_id, txid=txid, fee_btc=0.0001, script_type="p2wpkh", source="synthetic"))
        for i, a in enumerate(input_addrs):
            s.add(TxInput(transaction_id=tx_id, position=i, address=a, amount_btc=0.5))
        for i, a in enumerate(output_addrs):
            s.add(TxOutput(transaction_id=tx_id, position=i, address=a, amount_btc=0.4))
        if src_ip:
            s.add(FlowRecord(transaction_id=tx_id, src_ip=src_ip, dst_ip="198.51.100.1", src_port=40000, dst_port=dst_port,
                             geo_country=country, asn=asn, asn_org="Example ISP" if asn else None))


def test_refresh_entities_reports_no_rich_data_when_there_is_none(db):
    er = refresh_entities(db)
    assert er.rich_data_available is False
    assert er.entities == 0


def test_refresh_entities_builds_and_persists_an_entity(db):
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1", "addr2"], output_addrs=["addr3"], ts=datetime(2026, 1, 1))
    er = refresh_entities(db)
    assert er.rich_data_available is True
    assert er.entities == 1
    assert er.created == 1
    with db.session() as s:
        e = s.get(AddressEntity, "CIO-addr1")
        assert e is not None
        assert e.address_count == 2
        assert e.method == "common_input_ownership"
        assert e.total_sent_btc == 1.0
        members = set(s.scalars(select(AddressEntityMember.address).where(AddressEntityMember.entity_id == "CIO-addr1")))
        assert members == {"addr1", "addr2"}


def test_refresh_entities_is_idempotent(db):
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1", "addr2"], output_addrs=["addr3"], ts=datetime(2026, 1, 1))
    er1 = refresh_entities(db)
    er2 = refresh_entities(db)
    assert er1.entities == er2.entities == 1
    assert er2.created == 0
    with db.session() as s:
        assert s.get(AddressEntity, "CIO-addr1") is not None


def test_existing_wallet_level_leads_are_unaffected_by_entity_build(analysed_db):
    with analysed_db.session() as s:
        before = sorted(s.scalars(select(InvestigativeLead.wallet_address)))
    refresh_entities(analysed_db)  # analysed_db has no rich data -> no-op
    with analysed_db.session() as s:
        after = sorted(s.scalars(select(InvestigativeLead.wallet_address)))
    assert before == after


# ---------------------------------------------------------------------------------------------------------------
# 5. Correlation
# ---------------------------------------------------------------------------------------------------------------
def test_shared_ip_links_two_entities_and_raises_a_finding(db):
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1"], output_addrs=["addr9"],
                 ts=datetime(2026, 1, 1), src_ip="8.8.8.8", country="US", asn=15169)
    _seed_rich_tx(db, tx_id="t2", wallet_a="wC", wallet_b="wD", input_addrs=["addr2"], output_addrs=["addr8"],
                 ts=datetime(2026, 1, 2), src_ip="8.8.8.8", country="US", asn=15169)
    refresh_entities(db)
    cr = refresh_correlation(db)
    assert cr.rich_data_available is True
    assert cr.entity_links == 1
    assert cr.entity_ip_links == 2
    with db.session() as s:
        link = s.scalars(select(EntityLink)).first()
        assert {link.entity_a, link.entity_b} == {"CIO-addr1", "CIO-addr2"}
        finding = s.scalars(select(CorrelationFinding).where(CorrelationFinding.finding_type == "ip_used_by_several_entities")).first()
        assert finding is not None and "8.8.8.8" in finding.description


def test_entity_seen_from_many_countries_raises_a_finding(db):
    for i, (country, asn) in enumerate([("US", 1), ("DE", 2), ("JP", 3)]):
        _seed_rich_tx(db, tx_id=f"t{i}", wallet_a="wA", wallet_b=f"wR{i}", input_addrs=["addrX"], output_addrs=[f"out{i}"],
                     ts=datetime(2026, 1, 1) + timedelta(days=i), src_ip=f"8.8.8.{i}", country=country, asn=asn)
    refresh_entities(db)
    refresh_correlation(db)
    with db.session() as s:
        f = s.scalars(select(CorrelationFinding).where(CorrelationFinding.finding_type == "entity_many_countries")).first()
        assert f is not None and f.entity_id == "CIO-addrX"


def test_unusual_destination_port_raises_a_finding(db):
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1"], output_addrs=["addr2"],
                 ts=datetime(2026, 1, 1), src_ip="9.9.9.9", dst_port=9999, country="US", asn=1)
    refresh_entities(db)
    refresh_correlation(db)
    with db.session() as s:
        f = s.scalars(select(CorrelationFinding).where(CorrelationFinding.finding_type == "entity_unusual_port")).first()
        assert f is not None and 9999 in f.evidence["ports"]


def test_correlation_reports_no_rich_data_when_there_are_no_entities(db):
    cr = refresh_correlation(db)
    assert cr.rich_data_available is False


# ---------------------------------------------------------------------------------------------------------------
# 6. API
# ---------------------------------------------------------------------------------------------------------------
def test_entities_run_endpoint_reports_no_rich_data(client):
    r = client.post("/api/entities/run")
    assert r.status_code == 200
    assert r.json()["rich_data_available"] is False


def test_entities_list_and_detail_endpoints(client):
    db = client.app.state.db
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1", "addr2"], output_addrs=["addr3"],
                 ts=datetime(2026, 1, 1), src_ip="1.2.3.4", country="US", asn=7)
    r = client.post("/api/entities/run")
    assert r.status_code == 200
    assert r.json()["entities"] == 1

    r = client.get("/api/entities")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    eid = body["items"][0]["entity_id"]
    assert eid == "CIO-addr1"

    r = client.get(f"/api/entities/{eid}")
    assert r.status_code == 200
    detail = r.json()
    assert set(detail["addresses"]) == {"addr1", "addr2"}
    assert detail["ip_links"][0]["ip_address"] == "1.2.3.4"
    assert "not proof" in detail["note"]


def test_entities_list_filters(client):
    db = client.app.state.db
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1"], output_addrs=["addr9"],
                 ts=datetime(2026, 1, 1), src_ip="1.1.1.1", country="US", asn=1)
    _seed_rich_tx(db, tx_id="t2", wallet_a="wC", wallet_b="wD", input_addrs=["addr2", "addr3"], output_addrs=["addr8"],
                 ts=datetime(2026, 1, 2), src_ip="2.2.2.2", country="DE", asn=2)
    client.post("/api/entities/run")

    r = client.get("/api/entities", params={"min_addresses": 2})
    assert [i["entity_id"] for i in r.json()["items"]] == ["CIO-addr2"]

    r = client.get("/api/entities", params={"country": "DE"})
    assert [i["entity_id"] for i in r.json()["items"]] == ["CIO-addr2"]

    r = client.get("/api/entities", params={"ip": "1.1.1.1"})
    assert [i["entity_id"] for i in r.json()["items"]] == ["CIO-addr1"]

    r = client.get("/api/entities", params={"q": "addr3"})
    assert [i["entity_id"] for i in r.json()["items"]] == ["CIO-addr2"]


def test_entity_not_found_returns_uniform_error(client):
    r = client.get("/api/entities/CIO-nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_entity_graph_focus_on_entity(client):
    db = client.app.state.db
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1", "addr2"], output_addrs=["addr3"],
                 ts=datetime(2026, 1, 1), src_ip="5.5.5.5", country="US", asn=9)
    client.post("/api/entities/run")
    r = client.get("/api/entity-graph", params={"focus": "entity", "id": "CIO-addr1"})
    assert r.status_code == 200
    g = r.json()
    types = {n["type"] for n in g["nodes"]}
    assert {"entity", "address"} <= types
    assert g["focus"] == {"type": "entity", "id": "CIO-addr1"}


def test_entity_graph_focus_on_ip_and_address(client):
    db = client.app.state.db
    _seed_rich_tx(db, tx_id="t1", wallet_a="wA", wallet_b="wB", input_addrs=["addr1"], output_addrs=["addr2"],
                 ts=datetime(2026, 1, 1), src_ip="6.6.6.6", country="US", asn=9)
    client.post("/api/entities/run")
    assert client.get("/api/entity-graph", params={"focus": "ip", "id": "6.6.6.6"}).status_code == 200
    assert client.get("/api/entity-graph", params={"focus": "address", "id": "addr1"}).status_code == 200
    assert client.get("/api/entity-graph", params={"focus": "txid", "id": "t1"}).status_code == 200


def test_entity_graph_unknown_focus_id_is_404(client):
    r = client.get("/api/entity-graph", params={"focus": "entity", "id": "CIO-nope"})
    assert r.status_code == 404


def test_entity_graph_bad_node_type_is_422(client):
    r = client.get("/api/entity-graph", params={"focus": "entity", "id": "x", "node_types": "bogus"})
    assert r.status_code == 422


def test_entity_graph_bad_edge_type_is_422(client):
    r = client.get("/api/entity-graph", params={"focus": "entity", "id": "x", "edge_types": "bogus"})
    assert r.status_code == 422


def test_entity_graph_missing_id_is_422(client):
    r = client.get("/api/entity-graph", params={"focus": "entity"})
    assert r.status_code == 422


def test_entity_graph_truncates_and_reports_it(client):
    db = client.app.state.db
    addrs = [f"m{i:02d}" for i in range(20)]
    _seed_rich_tx(db, tx_id="big", wallet_a="wA", wallet_b="wB", input_addrs=addrs, output_addrs=["out1"], ts=datetime(2026, 1, 1))
    client.post("/api/entities/run")
    r = client.get("/api/entity-graph", params={"focus": "entity", "id": "CIO-m00", "max_nodes": 5})
    assert r.status_code == 200
    g = r.json()
    assert g["truncated"] is True
    assert sum(g["omitted"].values()) > 0
