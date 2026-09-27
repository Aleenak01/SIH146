"""
Tests for Phase 4 Part A's confidence score (backend/analysis/confidence.py, backend/services/confidence_queries.py,
backend/routers/insights.py). All data here is synthetic; no test needs the internet or the real GeoIP files.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from backend.analysis.confidence import (
    BASE_WEIGHT, CORRELATION_WEIGHT, ENTITY_WEIGHT, PATTERN_WEIGHT, compute_wallet_confidence, refresh_confidence,
)
from backend.analysis.peeling import refresh_peeling
from backend.models import (
    AddressEntityMember, ConfidenceScore, ConfidenceSignal, CorrelationFinding, Transaction, TxDetails, TxInput,
    TxOutput, Wallet,
)

T0 = datetime(2026, 1, 1)


# =================================================================================================================
# 1. Pure computation
# =================================================================================================================
def test_base_signal_only():
    c = compute_wallet_confidence("w1", combined_score=0.4, other_flagged_in_entity=0, correlation_finding_count=0, in_pattern=False)
    assert c.score == round(BASE_WEIGHT * 0.4, 6)
    assert [s.name for s in c.signals] == ["combined_score"]


def test_entity_co_flag_signal_adds_its_weight():
    c = compute_wallet_confidence("w1", combined_score=0.4, other_flagged_in_entity=2, correlation_finding_count=0, in_pattern=False)
    assert c.score == round(BASE_WEIGHT * 0.4 + ENTITY_WEIGHT, 6)
    assert [s.name for s in c.signals] == ["combined_score", "entity_co_flagged"]
    assert "2 other flagged" in c.signals[1].detail


def test_correlation_signal_adds_its_weight():
    c = compute_wallet_confidence("w1", combined_score=0.4, other_flagged_in_entity=0, correlation_finding_count=3, in_pattern=False)
    assert c.score == round(BASE_WEIGHT * 0.4 + CORRELATION_WEIGHT, 6)
    assert [s.name for s in c.signals] == ["combined_score", "correlation_findings"]


def test_pattern_signal_adds_its_weight():
    c = compute_wallet_confidence("w1", combined_score=0.4, other_flagged_in_entity=0, correlation_finding_count=0, in_pattern=True)
    assert c.score == round(BASE_WEIGHT * 0.4 + PATTERN_WEIGHT, 6)
    assert [s.name for s in c.signals] == ["combined_score", "pattern_involvement"]


def test_all_signals_combined_stay_within_zero_to_one():
    c = compute_wallet_confidence("w1", combined_score=1.0, other_flagged_in_entity=1, correlation_finding_count=1, in_pattern=True)
    assert BASE_WEIGHT + ENTITY_WEIGHT + CORRELATION_WEIGHT + PATTERN_WEIGHT == 1.0
    assert c.score == 1.0
    assert len(c.signals) == 4


def test_zero_combined_score_with_every_other_signal_is_still_bounded():
    c = compute_wallet_confidence("w1", combined_score=0.0, other_flagged_in_entity=5, correlation_finding_count=9, in_pattern=True)
    assert c.score == round(ENTITY_WEIGHT + CORRELATION_WEIGHT + PATTERN_WEIGHT, 6)
    assert c.score < 1.0


# =================================================================================================================
# 2. refresh_confidence: no analysis run yet
# =================================================================================================================
def test_refresh_confidence_reports_no_analysis_when_none_has_run(db):
    r = refresh_confidence(db)
    assert r.wallets == 0 and r.analysis_available is False
    with db.session() as s:
        assert s.scalar(select(ConfidenceScore.wallet_address).limit(1)) is None


def test_refresh_confidence_base_only_scores_every_scored_wallet(analysed_db):
    r = refresh_confidence(analysed_db)
    assert r.analysis_available is True and r.wallets > 0
    with analysed_db.session() as s:
        rows = list(s.scalars(select(ConfidenceScore)))
        assert len(rows) == r.wallets
        assert all(0.0 <= row.score <= BASE_WEIGHT + 1e-9 for row in rows)   # no rich/pattern data seeded yet: base only
        signal_names = {sig.signal_name for sig in s.scalars(select(ConfidenceSignal))}
        assert signal_names == {"combined_score"}


def test_refresh_confidence_is_idempotent(analysed_db):
    refresh_confidence(analysed_db)
    with analysed_db.session() as s:
        first = sorted((row.wallet_address, row.score) for row in s.scalars(select(ConfidenceScore)))
        first_signal_count = len(list(s.scalars(select(ConfidenceSignal))))
    refresh_confidence(analysed_db)
    with analysed_db.session() as s:
        second = sorted((row.wallet_address, row.score) for row in s.scalars(select(ConfidenceScore)))
        second_signal_count = len(list(s.scalars(select(ConfidenceSignal))))
    assert first == second
    assert first_signal_count == second_signal_count


# =================================================================================================================
# 3. refresh_confidence: entity co-flagging, correlation findings, pattern involvement (crafted on top of a real run)
# =================================================================================================================
def _mk_wallets(s, *wallets: str) -> None:
    for w in wallets:
        if s.get(Wallet, w) is None:
            s.add(Wallet(address=w, source="synthetic"))
    s.flush()


def _seed_rich_tx(s, *, tx_id: str, wallet: str, input_addrs: list[str], ts: datetime) -> None:
    """A rich transaction sent by `wallet`, spending `input_addrs` as inputs (so they union-find into one entity
    when two such transactions share an address)."""
    other = f"{wallet}-dst"
    _mk_wallets(s, wallet, other)
    s.add(Transaction(transaction_id=tx_id, timestamp=ts, sender_wallet=wallet, receiver_wallet=other,
                      amount_btc=1.0, input_count=len(input_addrs), output_count=1, source="synthetic"))
    s.flush()
    s.add(TxDetails(transaction_id=tx_id, txid=format(abs(hash(tx_id)) % (16 ** 64), "064x")[:64], fee_btc=0.0001, script_type="p2wpkh", source="synthetic"))
    for i, a in enumerate(input_addrs):
        s.add(TxInput(transaction_id=tx_id, position=i, address=a, amount_btc=0.5))
    s.add(TxOutput(transaction_id=tx_id, position=0, address=f"{tx_id}-out", amount_btc=0.999))


def test_entity_co_flagging_correlation_and_pattern_signals(analysed_client):
    """w000 is analysed_db's hub wallet and is reliably a lead (see seed_population). w004 is not, and (checked
    empirically for this fixed seed) is not naturally part of any peeling chain either, so it isolates the
    entity/correlation signals cleanly. Co-spending an address across a transaction sent by each of w000 and w004
    links them into one address entity, so w004 (not itself a lead) should pick up the entity_co_flagged signal
    because w000 (a lead) shares its entity. A manually recorded correlation finding on that entity gives both
    wallets the correlation_findings signal. The random population's own transfers already form many peeling
    chains (checked empirically), so pattern_involvement is exercised on real (not manually crafted) chain
    membership instead of one built just for this test; w030 (checked empirically to be in none of them, and not
    entity-linked here) isolates the "no additional signal" case."""
    db = analysed_client.app.state.db
    with db.transaction() as s:
        assert s.get(Wallet, "w000") is not None and s.get(Wallet, "w004") is not None
        # two rich transactions sharing address "shared1" as an input merge into one entity spanning w000 and w004
        _seed_rich_tx(s, tx_id="conf-r1", wallet="w000", input_addrs=["confA", "shared1"], ts=T0)
        _seed_rich_tx(s, tx_id="conf-r2", wallet="w004", input_addrs=["shared1", "confB"], ts=T0 + timedelta(hours=1))

    from backend.analysis.entities import refresh_entities

    er = refresh_entities(db)
    assert er.rich_data_available and er.entities >= 1
    with db.session() as s:
        eid = s.scalar(select(AddressEntityMember.entity_id).where(AddressEntityMember.address == "shared1"))
        assert eid is not None
        member_addrs = set(s.scalars(select(AddressEntityMember.address).where(AddressEntityMember.entity_id == eid)))
        assert {"confA", "shared1", "confB"} <= member_addrs

    with db.transaction() as s:
        s.add(CorrelationFinding(entity_id=eid, finding_type="entity_unusual_port", description="test finding", evidence={"ports": [1234]}, source="synthetic"))

    pr = refresh_peeling(db)     # the random population's own transfers already form many chains; nothing crafted here
    assert pr.chains > 0

    r = refresh_confidence(db)
    assert r.analysis_available is True

    with db.session() as s:
        by_wallet = {row.wallet_address: row.score for row in s.scalars(select(ConfidenceScore))}
        signals_by_wallet: dict[str, set[str]] = {}
        for sig in s.scalars(select(ConfidenceSignal)):
            signals_by_wallet.setdefault(sig.wallet_address, set()).add(sig.signal_name)

    assert "entity_co_flagged" in signals_by_wallet.get("w004", set())
    assert "correlation_findings" in signals_by_wallet.get("w004", set())
    assert "correlation_findings" in signals_by_wallet.get("w000", set())
    assert "pattern_involvement" in signals_by_wallet.get("w011", set())
    # w030: not entity-linked, not a peeling-chain wallet for this fixed seed -- only the base signal applies
    assert "w030" in by_wallet
    assert signals_by_wallet.get("w030", set()) == {"combined_score"}


# =================================================================================================================
# 4. API endpoints
# =================================================================================================================
def test_confidence_scores_list_and_detail_endpoints(analysed_client):
    refresh_confidence(analysed_client.app.state.db)

    r = analysed_client.get("/api/confidence-scores")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)                        # highest first
    assert all(i["signals"] for i in body["items"])                      # every wallet has at least the base signal

    wallet = body["items"][0]["wallet_address"]
    r = analysed_client.get(f"/api/wallets/{wallet}/confidence")
    assert r.status_code == 200
    detail = r.json()
    assert detail["wallet_address"] == wallet and "not statistically validated" in detail["label"]
    assert detail["signals"][0]["signal_name"] == "combined_score"


def test_confidence_endpoint_404_before_compute_confidence_has_run(analysed_client):
    r = analysed_client.get("/api/wallets/w000/confidence")
    assert r.status_code == 404 and "compute-confidence" in r.json()["error"]["message"]


def test_confidence_scores_list_min_score_filter(analysed_client):
    refresh_confidence(analysed_client.app.state.db)
    r = analysed_client.get("/api/confidence-scores", params={"min_score": 0.999})
    assert r.status_code == 200
    assert all(i["score"] >= 0.999 for i in r.json()["items"])
