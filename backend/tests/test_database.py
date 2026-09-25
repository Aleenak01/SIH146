"""Schema, constraints and initialization."""

from __future__ import annotations

import sys
from datetime import datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from backend.models import FEATURE_COLUMNS, Base, Transaction, Wallet

EXPECTED_TABLES = {
    "transactions", "wallets", "network_observations", "analysis_runs", "wallet_features", "anomaly_results",
    "forensic_findings", "investigative_leads", "entity_clusters", "entity_cluster_members", "cases", "case_items",
    "case_history",
}


def test_all_tables_created(db):
    assert set(inspect(db.engine).get_table_names()) == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_init_db_is_additive_and_repeatable(db):
    with db.transaction() as s:
        s.add(Wallet(address="w1", source="synthetic"))
    db.init_db()
    db.init_db()
    with db.session() as s:
        assert s.scalar(select(Wallet.address)) == "w1"


def test_feature_columns_match_the_existing_pipeline(project_root):
    sys.path.insert(0, str(project_root / "ml"))
    import feature_engineering as fe  # the unmodified existing module

    assert FEATURE_COLUMNS == fe.FEATURE_COLUMNS


def _tx(**kw):
    base = dict(transaction_id="t1", timestamp=datetime(2025, 10, 1), sender_wallet="a", receiver_wallet="b",
                amount_btc=1.0, input_count=1, output_count=1, source="synthetic", source_ref="r1")
    base.update(kw)
    return Transaction(**base)


@pytest.fixture
def two_wallets(db):
    with db.transaction() as s:
        s.add_all([Wallet(address="a", source="synthetic"), Wallet(address="b", source="synthetic")])


def test_foreign_keys_are_enforced(db, two_wallets):
    with pytest.raises(IntegrityError):
        with db.transaction() as s:
            s.add(_tx(receiver_wallet="ghost"))


@pytest.mark.parametrize("bad", [dict(amount_btc=0), dict(amount_btc=-1), dict(receiver_wallet="a"), dict(source="mainnet")])
def test_check_constraints(db, two_wallets, bad):
    with pytest.raises(IntegrityError):
        with db.transaction() as s:
            s.add(_tx(**bad))


def test_source_ref_is_unique_per_source(db, two_wallets):
    with db.transaction() as s:
        s.add(_tx())
    with pytest.raises(IntegrityError):
        with db.transaction() as s:
            s.add(_tx(transaction_id="t2"))          # same (source, source_ref)
    with db.transaction() as s:
        s.add(_tx(transaction_id="t3", source_ref=None))
        s.add(_tx(transaction_id="t4", source_ref=None))   # several NULL refs are allowed (API-posted transactions)
