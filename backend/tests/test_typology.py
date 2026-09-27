"""
Tests for Phase 4 Part A's typology tags (backend/services/typology.py, the /api/wallets/{id}/typology endpoint).
Read-only synthesis over data that already exists; no new table. All data here is synthetic.
"""

from __future__ import annotations

from datetime import datetime

from backend.models import (
    AddressEntity, AddressEntityMember, CoinJoinCandidate, CorrelationFinding, PeelingChain, PeelingChainHop,
    Transaction, Wallet,
)
from backend.services.typology import TAG_COINJOIN, TAG_CORRELATED_ENTITY, TAG_PEELING_CHAIN, wallet_typology

T0 = datetime(2026, 1, 1)


def _mk_wallets(s, *wallets: str) -> None:
    for w in wallets:
        if s.get(Wallet, w) is None:
            s.add(Wallet(address=w, source="synthetic"))
    s.flush()


def test_unknown_wallet_returns_none(db):
    with db.session() as s:
        assert wallet_typology(s, "nope") is None


def test_a_plain_wallet_has_no_tags(db):
    with db.transaction() as s:
        _mk_wallets(s, "w1")
    with db.session() as s:
        out = wallet_typology(s, "w1")
    assert out["wallet_address"] == "w1" and out["tags"] == []


def test_peeling_chain_tag(db):
    with db.transaction() as s:
        _mk_wallets(s, "A0", "A1", "A2", "A3")
        for tid, snd, rcv, amt in [("t1", "A0", "A1", 10.0), ("t2", "A1", "A2", 9.0), ("t3", "A2", "A3", 8.0)]:
            s.add(Transaction(transaction_id=tid, timestamp=T0, sender_wallet=snd, receiver_wallet=rcv, amount_btc=amt, input_count=1, output_count=1, source="synthetic"))
        s.flush()
        s.add(PeelingChain(chain_id="PEEL-t1", source="synthetic", start_wallet="A0", end_wallet="A3", hop_count=3, total_btc_start=10.0, total_btc_end=8.0))
        s.flush()
        s.add(PeelingChainHop(chain_id="PEEL-t1", hop_index=1, from_wallet="A0", to_wallet="A1", transaction_id="t1", amount_btc=10.0))
        s.add(PeelingChainHop(chain_id="PEEL-t1", hop_index=2, from_wallet="A1", to_wallet="A2", transaction_id="t2", amount_btc=9.0))
        s.add(PeelingChainHop(chain_id="PEEL-t1", hop_index=3, from_wallet="A2", to_wallet="A3", transaction_id="t3", amount_btc=8.0))
    with db.session() as s:
        out = wallet_typology(s, "A1")
    tags = {t["tag"] for t in out["tags"]}
    assert TAG_PEELING_CHAIN in tags
    assert "PEEL-t1" in next(t["reason"] for t in out["tags"] if t["tag"] == TAG_PEELING_CHAIN)
    with db.session() as s:
        assert wallet_typology(s, "A3")["tags"][0]["tag"] == TAG_PEELING_CHAIN     # the end wallet counts too


def test_coinjoin_tag(db):
    with db.transaction() as s:
        _mk_wallets(s, "wA", "wB")
        s.add(Transaction(transaction_id="cj1", timestamp=T0, sender_wallet="wA", receiver_wallet="wB", amount_btc=1.0, input_count=3, output_count=4, source="synthetic"))
        s.flush()
        s.add(CoinJoinCandidate(transaction_id="cj1", source="synthetic", input_count=3, output_count=4, equal_output_group_size=4, equal_output_value=0.25, score=0.9))
    with db.session() as s:
        out_sender = wallet_typology(s, "wA")
        out_receiver = wallet_typology(s, "wB")
    assert TAG_COINJOIN in {t["tag"] for t in out_sender["tags"]}
    assert TAG_COINJOIN in {t["tag"] for t in out_receiver["tags"]}


def test_correlated_entity_tag(db):
    with db.transaction() as s:
        _mk_wallets(s, "w1", "w1-dst")
        s.add(Transaction(transaction_id="e1", timestamp=T0, sender_wallet="w1", receiver_wallet="w1-dst", amount_btc=1.0, input_count=1, output_count=1, source="synthetic"))
        s.flush()
        from backend.models import TxInput

        s.add(TxInput(transaction_id="e1", position=0, address="addrX", amount_btc=1.0))
        s.add(AddressEntity(entity_id="CIO-addrX", method="common_input_ownership", source="synthetic", address_count=1))
        s.flush()
        s.add(AddressEntityMember(entity_id="CIO-addrX", address="addrX"))
        s.add(CorrelationFinding(entity_id="CIO-addrX", finding_type="entity_unusual_port", description="test", evidence={}, source="synthetic"))
    with db.session() as s:
        out = wallet_typology(s, "w1")
    tags = {t["tag"] for t in out["tags"]}
    assert TAG_CORRELATED_ENTITY in tags
    assert "1 recorded" in next(t["reason"] for t in out["tags"] if t["tag"] == TAG_CORRELATED_ENTITY)


def test_a_wallet_can_carry_more_than_one_tag(db):
    with db.transaction() as s:
        _mk_wallets(s, "wA", "wB")
        s.add(Transaction(transaction_id="cj1", timestamp=T0, sender_wallet="wA", receiver_wallet="wB", amount_btc=1.0, input_count=3, output_count=4, source="synthetic"))
        s.flush()
        s.add(CoinJoinCandidate(transaction_id="cj1", source="synthetic", input_count=3, output_count=4, equal_output_group_size=4, equal_output_value=0.25, score=0.9))
        s.add(PeelingChain(chain_id="PEEL-cj1", source="synthetic", start_wallet="wA", end_wallet="wB", hop_count=3, total_btc_start=1.0, total_btc_end=1.0))
        s.flush()
        s.add(PeelingChainHop(chain_id="PEEL-cj1", hop_index=1, from_wallet="wA", to_wallet="wB", transaction_id="cj1", amount_btc=1.0))
    with db.session() as s:
        tags = {t["tag"] for t in wallet_typology(s, "wA")["tags"]}
    assert tags == {TAG_PEELING_CHAIN, TAG_COINJOIN}


def test_typology_endpoint(client):
    db = client.app.state.db
    with db.transaction() as s:
        _mk_wallets(s, "w1")
    r = client.get("/api/wallets/w1/typology")
    assert r.status_code == 200
    body = r.json()
    assert body == {"wallet_address": "w1", "tags": [], "note": body["note"]}
    assert "never proof" in body["note"]


def test_typology_endpoint_404_for_unknown_wallet(client):
    r = client.get("/api/wallets/nope/typology")
    assert r.status_code == 404
