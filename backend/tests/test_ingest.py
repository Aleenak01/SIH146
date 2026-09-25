"""Ingestion core: validation, deduplication, wallet creation/update."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.ingestion.base import SourcedTransaction
from backend.models import Transaction, Wallet
from backend.schemas import TransactionIn
from backend.services.ingest import ingest, ingest_payloads


def tx(sender="a", receiver="b", ts="2025-10-01T08:00:00", amount=1.0, **kw):
    return TransactionIn(sender_wallet=sender, receiver_wallet=receiver, timestamp=ts, amount_btc=amount, **kw)


def good(sender="a", receiver="b", ts="2025-10-01T08:00:00", amount=1.0, **kw):
    return dict(sender_wallet=sender, receiver_wallet=receiver, timestamp=ts, amount_btc=amount, **kw)


# ---- validation -------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    dict(amount_btc=0), dict(amount_btc=-0.5), dict(amount_btc=float("nan")), dict(amount_btc=float("inf")),
    dict(amount_btc=0.000000001),                       # below one satoshi
    dict(amount_btc=22_000_000),                        # more than exists
    dict(receiver_wallet="a"),                          # sends to itself
    dict(sender_wallet=""), dict(sender_wallet="has space"), dict(sender_wallet="x" * 200), dict(receiver_wallet="a;drop table"),
    dict(timestamp="not a date"), dict(input_count=0), dict(output_count=-1), dict(input_count=10**6),
    dict(source="real_bitcoin"),                         # unknown field: the server, not the client, decides the source
])
def test_invalid_transactions_are_rejected(bad):
    with pytest.raises(ValidationError):
        TransactionIn(**{**good(), **bad})


def test_missing_required_fields_rejected():
    with pytest.raises(ValidationError):
        TransactionIn(sender_wallet="a", receiver_wallet="b")


def test_timezone_is_normalised_to_naive_utc_and_amount_to_satoshis():
    t = TransactionIn(**good(ts="2025-10-01T10:00:00+02:00", amount=0.123456789))
    assert t.timestamp == datetime(2025, 10, 1, 8, 0, 0) and t.timestamp.tzinfo is None
    assert t.amount_btc == 0.12345679


# ---- persistence ------------------------------------------------------------------------------
def test_ingest_creates_transactions_and_wallets(db):
    with db.transaction() as s:
        out = ingest(s, [SourcedTransaction(tx("a", "b", "2025-10-05T00:00:00")), SourcedTransaction(tx("b", "c", "2025-10-01T00:00:00"))], "synthetic")
    assert (out.inserted, out.duplicates, out.rejected) == (2, 0, [])
    assert out.transaction_ids == ["syn-000001", "syn-000002"]      # allocated in order
    with db.session() as s:
        wallets = {w.address: w for w in s.scalars(select(Wallet))}
        assert set(wallets) == {"a", "b", "c"} and all(w.source == "synthetic" for w in wallets.values())
        assert wallets["b"].first_seen == datetime(2025, 10, 1) and wallets["b"].last_seen == datetime(2025, 10, 5)


def test_existing_wallets_are_updated_not_duplicated(db):
    with db.transaction() as s:
        ingest(s, [SourcedTransaction(tx("a", "b", "2025-10-10T00:00:00"))], "synthetic")
    with db.transaction() as s:
        ingest(s, [SourcedTransaction(tx("a", "c", "2025-10-01T00:00:00")), SourcedTransaction(tx("c", "a", "2025-12-01T00:00:00"))], "synthetic")
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Wallet)) == 3
        a = s.get(Wallet, "a")
        assert (a.first_seen, a.last_seen) == (datetime(2025, 10, 1), datetime(2025, 12, 1))


def test_synthetic_id_sequence_continues(db):
    for _ in range(2):
        with db.transaction() as s:
            out = ingest(s, [SourcedTransaction(tx(ts=f"2025-10-0{i}T00:00:00", amount=i)) for i in (1, 2)], "synthetic")
    with db.session() as s:
        assert sorted(s.scalars(select(Transaction.transaction_id))) == ["syn-000001", "syn-000002", "syn-000003", "syn-000004"]


def test_duplicate_ids_and_refs(db):
    item = SourcedTransaction(tx(transaction_id="syn-000100"), "row:2")
    with db.transaction() as s:
        assert ingest(s, [item], "synthetic").inserted == 1
    with db.transaction() as s:
        again = ingest(s, [item, item], "synthetic")                                    # identical: harmless duplicate
        clash = ingest(s, [SourcedTransaction(tx(transaction_id="syn-000100", amount=9))], "synthetic")   # same id, different content
        ref = ingest(s, [SourcedTransaction(tx(sender="x", receiver="y"), "row:2")], "synthetic")           # same source_ref
    assert (again.inserted, again.duplicates) == (0, 2)
    assert clash.inserted == 0 and "different content" in clash.rejected[0]["error"]
    assert (ref.inserted, ref.duplicates) == (0, 1)


def test_real_transactions_need_an_id_and_cannot_share_wallets_with_synthetic(db):
    with db.transaction() as s:
        out = ingest(s, [SourcedTransaction(tx())], "real_bitcoin")
    assert out.inserted == 0 and "transaction_id is required" in out.rejected[0]["error"]

    with db.transaction() as s:
        ingest(s, [SourcedTransaction(tx("a", "b"))], "synthetic")
    with db.transaction() as s:
        out = ingest(s, [SourcedTransaction(tx("a", "z", transaction_id="abc123"))], "real_bitcoin")
    assert out.inserted == 0 and "different data source" in out.rejected[0]["error"]


def test_unknown_source_is_an_error(db):
    with pytest.raises(ValueError):
        with db.transaction() as s:
            ingest(s, [SourcedTransaction(tx())], "mainnet")


def test_malformed_items_never_stop_a_batch_and_keep_their_index(db):
    payloads = [good(), {"amount_btc": -1}, good("b", "c", amount=2), {}, "not an object", None, good("c", "a", amount=3, ts="bad"), good("c", "d", amount=4)]
    with db.transaction() as s:
        out = ingest_payloads(s, payloads)
    assert out.inserted == 3
    assert [r["index"] for r in out.rejected] == [1, 3, 4, 5, 6]
    assert all(r["error"] for r in out.rejected)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 3


def test_a_failed_batch_leaves_no_partial_data(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as s:
            ingest(s, [SourcedTransaction(tx())], "synthetic")
            raise RuntimeError("boom")
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0
        assert s.scalar(select(func.count()).select_from(Wallet)) == 0
