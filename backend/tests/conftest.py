"""Shared fixtures. Every test gets its own SQLite file in a temp directory; the real database is never touched."""

from __future__ import annotations

import csv
import random
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import PROJECT_ROOT, Settings
from backend.database import Database
from backend.main import create_app

RAW_HEADER = ["timestamp", "wallet_address", "amount_btc", "direction", "input_count", "output_count", "counterparty_wallet"]

# (timestamp, sender, receiver, amount, inputs, outputs) - 6 transfers between 5 wallets
SMALL_TRANSFERS = [
    ("2025-10-01 08:00:00", "wallet_A", "wallet_B", 0.5, 1, 2),
    ("2025-10-02 09:30:00", "wallet_B", "wallet_C", 0.25, 2, 1),
    ("2025-10-02 09:30:00", "wallet_A", "wallet_C", 1.0, 1, 1),      # same timestamp as the row above
    ("2025-11-15 12:00:00", "wallet_C", "wallet_D", 0.1, 3, 2),
    ("2026-03-03 18:45:10", "wallet_D", "wallet_A", 2.0, 1, 1),
    ("2026-09-24 21:00:00", "wallet_E", "wallet_A", 0.05, 1, 3),
]


def write_raw_csv(path: Path, transfers=SMALL_TRANSFERS, *, mirror: bool = True, header=RAW_HEADER) -> Path:
    """Write transfers in the project's raw format: an Outgoing row plus a mirrored Incoming row each."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for ts, snd, rcv, amt, n_in, n_out in transfers:
            w.writerow([ts, snd, amt, "Outgoing", n_in, n_out, rcv])
            if mirror:
                w.writerow([ts, rcv, amt, "Incoming", n_in, n_out, snd])
    return path


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        dataset_csv=write_raw_csv(tmp_path / "raw.csv"),
        host="127.0.0.1",
        port=8000,
        cors_origins=[],
        monitor_autostart=False,                  # tests drive the monitor explicitly
        monitor_interval=1.0,
        inbox_dir=tmp_path / "inbox",
        network_csv=tmp_path / "network_obs.csv",      # never the real data/ file
    )


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.database_url)
    database.init_db()
    yield database
    database.dispose()


@pytest.fixture
def client(settings) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def loaded_client(client) -> TestClient:
    """A client whose database already holds the small fixture dataset."""
    r = client.post("/api/import/synthetic-csv", json={})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


# ---------------------------------------------------------------------------------------------------
# A small, seeded population (fast to analyse) and an analysed copy of the real project dataset (slow, built once)
# ---------------------------------------------------------------------------------------------------
def seed_population(db: Database, n_wallets: int = 60, n_transfers: int = 700, seed: int = 7, hub: bool = True) -> list[str]:
    """
    Fill the database with a reproducible random network. Every wallet has at least two transactions (a ring), most
    activity is between random pairs, and one 'hub' wallet receives from many different wallets so the model and the
    rules have something to find. Returns the wallet addresses.
    """
    from backend.services.ingest import ingest_payloads

    rng = random.Random(seed)
    wallets = [f"w{i:03d}" for i in range(n_wallets)]
    start = datetime(2025, 10, 1)
    payloads = []

    def add(s, r, amount=None):
        payloads.append({
            "sender_wallet": s, "receiver_wallet": r, "timestamp": (start + timedelta(minutes=rng.randint(0, 60 * 24 * 300))).isoformat(),
            "amount_btc": round(amount if amount is not None else rng.lognormvariate(-3, 0.8), 8), "input_count": rng.randint(1, 3), "output_count": rng.randint(1, 3),
        })

    for i, w in enumerate(wallets):                                   # ring: two transactions per wallet
        add(w, wallets[(i + 1) % n_wallets])
        add(wallets[(i - 1) % n_wallets], w)
    for _ in range(n_transfers):
        a, b = rng.sample(wallets, 2)
        add(a, b)
    if hub:
        for w in rng.sample(wallets[1:], min(45, len(wallets) - 1)):
            for _ in range(rng.randint(1, 3)):
                add(w, wallets[0], rng.uniform(0.5, 3))              # wallets[0] is the hub
    with db.transaction() as s:
        out = ingest_payloads(s, payloads)
    assert not out.rejected
    return wallets


@pytest.fixture
def population_db(db) -> Database:
    seed_population(db)
    return db


@pytest.fixture
def analysed_db(population_db) -> Database:
    from backend.analysis.service import run_analysis

    run_analysis(population_db)
    return population_db


@pytest.fixture
def networked_db(population_db, tmp_path) -> Database:
    """The small population with the batch-generated (deterministic) synthetic network dataset: some wallets share devices."""
    from backend.services import network as ns

    path = tmp_path / "obs.csv"
    ns.generate_csv(population_db, path)
    ns.import_csv(population_db, path, replace=True)
    return population_db


@pytest.fixture
def networked_analysed_db(networked_db) -> Database:
    from backend.analysis.service import run_analysis

    run_analysis(networked_db)
    return networked_db


@pytest.fixture
def networked_client(settings, networked_analysed_db) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def analysed_client(settings, analysed_db) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="session")
def real_analysed_template(tmp_path_factory, project_root):
    """The real dataset imported and analysed once per test session (about a minute on a slow machine)."""
    from backend.analysis.service import run_analysis
    from backend.services.importer import import_synthetic_csv

    path = tmp_path_factory.mktemp("real_template") / "real.db"
    database = Database(f"sqlite:///{path.as_posix()}")
    database.init_db()
    import_synthetic_csv(database, project_root / "dataset" / "synthetic_bitcoin_transactions.csv")
    from backend.services import network as ns

    obs_csv = path.parent / "obs.csv"
    ns.generate_csv(database, obs_csv)
    ns.import_csv(database, obs_csv)
    summary = run_analysis(database)             # also refreshes the clusters
    database.dispose()
    return path, summary


def copy_sqlite(src: Path, dst: Path) -> None:
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    with b:
        a.backup(b)
    a.close()
    b.close()


@pytest.fixture
def real_db(real_analysed_template, tmp_path) -> Database:
    """A private, mutable copy of the analysed real dataset."""
    path, _ = real_analysed_template
    dst = tmp_path / "real_copy.db"
    copy_sqlite(path, dst)
    database = Database(f"sqlite:///{dst.as_posix()}")
    yield database
    database.dispose()


@pytest.fixture
def real_client(settings, real_db) -> TestClient:
    from dataclasses import replace

    with TestClient(create_app(replace(settings, db_path=Path(real_db.url.removeprefix("sqlite:///"))))) as c:
        yield c
