"""Shared fixtures. Every test gets its own SQLite file in a temp directory; the real database is never touched."""

from __future__ import annotations

import csv
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
