"""Importing the existing synthetic CSV (small fixtures plus the real project dataset)."""

from __future__ import annotations

import csv
from datetime import datetime

import pytest
from sqlalchemy import func, select

from backend.ingestion.base import DatasetError
from backend.ingestion.synthetic_csv import SyntheticCSVSource
from backend.models import Case, Transaction, Wallet
from backend.services.importer import ImportConflict, import_synthetic_csv

from .conftest import RAW_HEADER, SMALL_TRANSFERS, write_raw_csv


def test_mirrored_rows_become_single_chronological_transactions(settings):
    items = SyntheticCSVSource(settings.dataset_csv).fetch()
    assert len(items) == len(SMALL_TRANSFERS)                      # 12 CSV rows -> 6 transactions
    assert [i.transaction.transaction_id for i in items] == [f"syn-{n:06d}" for n in range(1, 7)]
    assert [i.transaction.timestamp for i in items] == sorted(i.transaction.timestamp for i in items)
    first = items[0]
    assert (first.transaction.sender_wallet, first.transaction.receiver_wallet, first.transaction.amount_btc) == ("wallet_A", "wallet_B", 0.5)
    assert first.source_ref == "row:2"                              # first data line of the file (header is line 1)
    # identical timestamps keep file order (the B->C row precedes the A->C row in the file)
    assert [i.transaction.receiver_wallet for i in items[1:3]] == ["wallet_C", "wallet_C"]
    assert [i.transaction.sender_wallet for i in items[1:3]] == ["wallet_B", "wallet_A"]


def test_import_is_idempotent(db, settings):
    a = import_synthetic_csv(db, settings.dataset_csv)
    b = import_synthetic_csv(db, settings.dataset_csv)
    assert (a.inserted, a.skipped_existing, a.wallets_total) == (6, 0, 5)
    assert (b.inserted, b.skipped_existing, b.wallets_total) == (0, 6, 5)


def test_changed_csv_conflicts_unless_replace(db, settings, tmp_path):
    import_synthetic_csv(db, settings.dataset_csv)
    changed = list(SMALL_TRANSFERS)
    changed[0] = ("2025-10-01 08:00:00", "wallet_A", "wallet_B", 0.75, 1, 2)      # different amount on the same row
    write_raw_csv(settings.dataset_csv, changed)

    with pytest.raises(ImportConflict):
        import_synthetic_csv(db, settings.dataset_csv)
    with db.session() as s:                                                        # nothing was changed by the failed import
        assert s.scalar(select(Transaction.amount_btc).where(Transaction.transaction_id == "syn-000001")) == 0.5

    r = import_synthetic_csv(db, settings.dataset_csv, replace=True)
    assert r.replaced_existing and r.inserted == 6
    with db.session() as s:
        assert s.scalar(select(Transaction.amount_btc).where(Transaction.transaction_id == "syn-000001")) == 0.75
        assert s.scalar(select(func.count()).select_from(Transaction)) == 6


def test_replace_never_deletes_cases(db, settings):
    import_synthetic_csv(db, settings.dataset_csv)
    with db.transaction() as s:
        s.add(Case(case_id="CASE-0001", title="kept"))
    import_synthetic_csv(db, settings.dataset_csv, replace=True)
    with db.session() as s:
        assert s.get(Case, "CASE-0001") is not None


@pytest.mark.parametrize("build,message", [
    (lambda p: write_raw_csv(p, mirror=False), "not mirrored"),
    (lambda p: write_raw_csv(p, header=RAW_HEADER[:-1] + ["other"]), "Unexpected columns"),
])
def test_bad_files_are_refused_before_anything_is_written(db, tmp_path, build, message):
    path = build(tmp_path / "bad.csv")
    with pytest.raises(DatasetError, match=message):
        import_synthetic_csv(db, path)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0


def test_missing_values_and_bad_directions_are_refused(tmp_path):
    p = write_raw_csv(tmp_path / "x.csv")
    rows = list(csv.reader(p.open(encoding="utf-8")))
    rows[1][2] = ""                                             # empty amount
    with p.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    with pytest.raises(DatasetError, match="missing values"):
        SyntheticCSVSource(p).fetch()

    p2 = write_raw_csv(tmp_path / "y.csv")
    p2.write_text(p2.read_text(encoding="utf-8").replace("Outgoing", "Sideways", 1), encoding="utf-8")
    with pytest.raises(DatasetError, match="direction"):
        SyntheticCSVSource(p2).fetch()


def test_missing_file(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        SyntheticCSVSource(tmp_path / "nope.csv").fetch()


# ---- the real project dataset ------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_db(tmp_path_factory, project_root):
    from backend.database import Database

    csv_path = project_root / "dataset" / "synthetic_bitcoin_transactions.csv"
    if not csv_path.is_file():
        pytest.skip("project dataset not present")
    database = Database(f"sqlite:///{(tmp_path_factory.mktemp('real') / 'real.db').as_posix()}")
    database.init_db()
    result = import_synthetic_csv(database, csv_path)
    yield database, result
    database.dispose()


def test_real_dataset_counts(real_db):
    db, result = real_db
    assert (result.transfers_in_file, result.inserted, result.wallets_total) == (5000, 5000, 410)
    with db.session() as s:
        first, last = s.execute(select(func.min(Transaction.timestamp), func.max(Transaction.timestamp))).one()
    assert first >= datetime(2025, 10, 1) and last <= datetime(2026, 9, 24, 23, 59, 59)


def test_real_dataset_matches_the_pipelines_own_feature_file(real_db, project_root):
    """Independent cross-check: per-wallet counts in SQLite equal the transaction_count features the ML pipeline computed."""
    db, _ = real_db
    features = {r["wallet_address"]: r for r in csv.DictReader((project_root / "data" / "wallet_behavior_features.csv").open(encoding="utf-8"))}
    with db.session() as s:
        sent = dict(s.execute(select(Transaction.sender_wallet, func.count()).group_by(Transaction.sender_wallet)).all())
        received = dict(s.execute(select(Transaction.receiver_wallet, func.count()).group_by(Transaction.receiver_wallet)).all())
        wallets = set(s.scalars(select(Wallet.address)))
    assert wallets == set(features)
    for w, row in features.items():
        assert sent.get(w, 0) == int(row["outgoing_count"]), w
        assert received.get(w, 0) == int(row["incoming_count"]), w
        assert sent.get(w, 0) + received.get(w, 0) == int(row["transaction_count"]), w
