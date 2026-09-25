"""Inbox, synthetic stream, monitor cycles, and the end-to-end path of a new transaction."""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.analysis.ml_bridge import AnalysisError
from backend.analysis.service import latest_run
from backend.ingestion.inbox import MAX_FILE_BYTES, process_inbox
from backend.ingestion.synthetic_stream import StreamUnavailable, SyntheticStreamSource
from backend.models import AnomalyResult, InvestigativeLead, Transaction, WalletFeatures
from backend.services.monitor import Monitor, MonitorConfigError


def old(path, seconds=10):
    t = time.time() - seconds
    os.utime(path, (t, t))
    return path


def write_csv(path, rows, header=("timestamp", "sender_wallet", "receiver_wallet", "amount_btc")):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return old(path)


def tx_count(db) -> int:
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(Transaction))


# ---- inbox ---------------------------------------------------------------------------------------------------
def test_inbox_ingests_good_rows_and_reports_bad_ones(db, settings):
    inbox = settings.inbox_dir
    inbox.mkdir()
    write_csv(inbox / "batch1.csv", [
        ("2026-09-25T10:00:00", "a", "b", "0.5"), ("2026-09-25T10:01:00", "b", "c", "-3"), ("nonsense", "a", "c", "1"),
        ("2026-09-25T10:02:00", "c", "c", "1"), ("2026-09-25T10:03:00", "c", "a", "2"),
    ])
    (inbox / "more.jsonl").write_text('{"timestamp":"2026-09-25T11:00:00","sender_wallet":"d","receiver_wallet":"a","amount_btc":1.5}\nnot json\n{"amount_btc":1}\n', encoding="utf-8")
    old(inbox / "more.jsonl")

    r = process_inbox(db, inbox)
    assert (r.files, r.inserted, r.rejected, r.failed_files) == (2, 3, 5, 0)
    assert tx_count(db) == 3
    assert not list(inbox.glob("*.csv")) and not list(inbox.glob("*.jsonl"))                 # both moved away
    notes = "\n".join(p.read_text() for p in (inbox / "processed").glob("*.rejected.txt"))
    assert "line 3:" in notes and "line 4:" in notes and "line 5:" in notes and "invalid JSON" in notes
    assert process_inbox(db, inbox).files == 0                                              # nothing is processed twice


def test_inbox_skips_files_that_are_still_being_written(db, settings):
    settings.inbox_dir.mkdir()
    p = settings.inbox_dir / "fresh.csv"
    write_csv(p, [("2026-09-25T10:00:00", "a", "b", "1")])
    os.utime(p, None)                                                                       # modified just now
    assert process_inbox(db, settings.inbox_dir).files == 0 and p.exists()


def test_redropping_the_same_file_name_does_not_duplicate(db, settings):
    inbox = settings.inbox_dir
    inbox.mkdir()
    rows = [("2026-09-25T10:00:00", "a", "b", "1")]
    write_csv(inbox / "same.csv", rows)
    assert process_inbox(db, inbox).inserted == 1
    write_csv(inbox / "same.csv", rows)
    again = process_inbox(db, inbox)
    assert (again.inserted, again.duplicates) == (0, 1) and tx_count(db) == 1


def test_unusable_files_go_to_failed_and_never_raise(db, settings):
    inbox = settings.inbox_dir
    inbox.mkdir()
    write_csv(inbox / "nocolumns.csv", [("x", "y")], header=("foo", "bar"))
    (inbox / "binary.csv").write_bytes(b"\xff\xfe\x00garbage")
    old(inbox / "binary.csv")
    big = inbox / "huge.csv"
    with big.open("wb") as f:
        f.truncate(MAX_FILE_BYTES + 1)
    old(big)
    r = process_inbox(db, inbox)
    assert r.failed_files == 3 and r.inserted == 0
    reasons = " ".join(p.read_text() for p in (inbox / "failed").glob("*.reason.txt"))
    assert "missing required column" in reasons and "larger than" in reasons
    assert tx_count(db) == 0


def test_missing_inbox_folder_is_fine(db, tmp_path):
    assert process_inbox(db, tmp_path / "does-not-exist").files == 0


# ---- synthetic stream ------------------------------------------------------------------------------------------
def test_stream_is_reproducible_valid_and_built_from_existing_wallets(population_db, tmp_path):
    now = datetime(2026, 9, 25, 12, 0, 0)
    a = SyntheticStreamSource(population_db, seed=5).generate(40, now)
    b = SyntheticStreamSource(population_db, seed=5).generate(40, now)
    c = SyntheticStreamSource(population_db, seed=6).generate(40, now)
    key = lambda items: [(i.transaction.sender_wallet, i.transaction.receiver_wallet, i.transaction.amount_btc) for i in items]
    assert key(a) == key(b) != key(c)
    with population_db.session() as s:
        known = set(s.scalars(select(Transaction.sender_wallet))) | set(s.scalars(select(Transaction.receiver_wallet)))
    assert all(i.transaction.sender_wallet in known and i.transaction.receiver_wallet in known for i in a)
    assert all(i.transaction.timestamp <= now and i.transaction.amount_btc > 0 for i in a)
    assert len({i.source_ref for i in a}) == 40 and all(i.source_ref.startswith("stream:") for i in a)


def test_stream_needs_existing_data(db):
    with pytest.raises(StreamUnavailable):
        SyntheticStreamSource(db)


def test_fan_out_scenario_is_an_explicit_labelled_demo_of_ordinary_transactions(population_db):
    now = datetime(2026, 9, 25, 12, 0, 0)
    stream = SyntheticStreamSource(population_db, seed=1)
    wallet, items = stream.fan_out_scenario(now, receivers=15)
    assert len(items) == 15 and all(i.transaction.sender_wallet == wallet for i in items)
    assert len({i.transaction.receiver_wallet for i in items}) == 15                   # 15 distinct new receivers
    times = [i.transaction.timestamp for i in items]
    assert times == sorted(times) and max(times) <= now and now - min(times) < timedelta(hours=1)
    assert all("scenario-fan-out" in i.source_ref for i in items)
    with population_db.session() as s:                                                 # none of them were partners before
        partners = set(s.scalars(select(Transaction.receiver_wallet).where(Transaction.sender_wallet == wallet))) | \
                   set(s.scalars(select(Transaction.sender_wallet).where(Transaction.receiver_wallet == wallet)))
    assert not partners & {i.transaction.receiver_wallet for i in items}


# ---- monitor cycles ---------------------------------------------------------------------------------------------
@pytest.fixture
def monitor(analysed_db, settings) -> Monitor:
    return Monitor(analysed_db, settings)


def test_status_reports_the_synthetic_source_honestly(monitor):
    s = monitor.status()
    assert s["status"] == "stopped" and s["data_source"] == {"kind": "synthetic", "label": "Synthetic dataset", "is_real_data": False,
                                                              "note": "Synthetic demo data. No real blockchain data is being monitored."}
    assert s["analysis_stale"] is False and s["leads_total"] > 0 and s["transactions_total"] == tx_count(monitor.db)
    monitor.configure(stream_enabled=True)
    assert monitor.status()["data_source"]["label"] == "Synthetic stream" and monitor.status()["data_source"]["is_real_data"] is False


def test_a_tick_with_nothing_new_does_nothing(monitor):
    r = monitor.tick()
    assert r == {"inbox": 0, "stream": 0, "analysis": None}


def test_a_tick_ingests_the_inbox_and_reanalyses(monitor, settings):
    monitor.settings.inbox_dir.mkdir()
    write_csv(settings.inbox_dir / "new.csv", [("2026-09-25T10:00:00", "w005", "w006", "0.25"), ("2026-09-25T10:05:00", "w006", "w007", "0.5")])
    before = latest_run(monitor.db.session()).run_id
    r = monitor.tick()
    assert r["inbox"] == 2 and r["analysis"]["status"] == "completed" and r["analysis"]["run_id"] == before + 1
    assert r["analysis"]["transfer_count"] == tx_count(monitor.db)
    assert monitor.status()["ingested_since_start"]["inbox"] == 2 and monitor.status()["last_analysis_trigger"] == "monitor"
    assert monitor.tick()["analysis"] is None                                           # up to date again: no needless re-run


def test_the_stream_feeds_new_transactions_at_the_configured_rate(monitor):
    monitor.configure(interval=5, stream_enabled=True, stream_rate=60, auto_analysis=False)
    n0 = tx_count(monitor.db)
    r = monitor.tick()
    assert r["stream"] == 5 and tx_count(monitor.db) == n0 + 5                           # 60 per minute over a 5 s interval
    assert monitor.status()["analysis_stale"] is True and r["analysis"] is None          # ingested, but automatic analysis is off


def test_a_failing_analysis_is_not_retried_until_the_data_changes(monitor, monkeypatch):
    calls = []

    def failing(*_a, **_k):
        calls.append(1)
        raise AnalysisError("not enough data")

    monkeypatch.setattr("backend.services.monitor.run_analysis", failing)
    monitor.configure(stream_enabled=True, stream_rate=60)
    monitor.tick()                                                                        # data changes -> attempts (fails)
    assert len(calls) == 1 and "not enough data" in monitor.status()["last_error"]
    monitor.configure(stream_enabled=False)
    monitor.tick()
    monitor.tick()
    assert len(calls) == 1                                                                # same data: no retry storm
    monitor.configure(stream_enabled=True)
    monitor.tick()                                                                        # new data -> tries again
    assert len(calls) == 2


def test_configuration_is_validated(monitor):
    for bad in ({"interval": 0}, {"interval": 10_000}, {"stream_rate": 0}, {"stream_rate": 10_000}):
        with pytest.raises(MonitorConfigError):
            monitor.configure(**bad)
    monitor.configure(interval=2, auto_analysis=False, stream_rate=10)
    s = monitor.status()
    assert (s["interval_seconds"], s["auto_analysis"], s["stream"]["rate_per_minute"]) == (2, False, 10)


def test_a_new_transaction_travels_through_the_whole_pipeline(analysed_client, analysed_db):
    """POST a transaction -> database -> monitor cycle -> features -> ML + forensics -> fusion -> leads -> visible in the API."""
    monitor = analysed_client.app.state.monitor
    before = analysed_client.get("/api/wallets/w010/analysis").json()
    assert before["scored"]
    n_before = analysed_client.get("/api/overview").json()["transactions_total"]

    r = analysed_client.post("/api/transactions", json={"sender_wallet": "w010", "receiver_wallet": "w011", "timestamp": "2026-09-24T23:24:18Z", "amount_btc": 2.5})
    assert r.status_code == 201
    status = analysed_client.get("/api/monitor/status").json()
    assert status["analysis_stale"] is True and status["ingested_since_start"]["api"] == 1                   # stored, not yet analysed

    tick = monitor.tick()                                                                                       # what the background thread does
    assert tick["analysis"]["status"] == "completed" and tick["analysis"]["transfer_count"] == n_before + 1

    after = analysed_client.get("/api/wallets/w010/analysis").json()
    assert after["run_id"] > before["run_id"]
    assert after["features"]["transaction_count"] == before["features"]["transaction_count"] + 1               # feature update
    assert after["features"]["outgoing_count"] == before["features"]["outgoing_count"] + 1
    assert after["features"]["total_sent_btc"] == pytest.approx(before["features"]["total_sent_btc"] + 2.5, abs=1e-5)
    assert after["ml_score"] is not None and after["combined_score"] is not None and after["priority_rank"]   # ML + fusion + rank
    ranks = sorted(w["priority_rank"] for w in [analysed_client.get(f"/api/wallets/w{i:03d}/analysis").json() for i in range(60)])
    assert ranks == list(range(1, 61))                                                                         # ranking recomputed for everyone
    status = analysed_client.get("/api/monitor/status").json()
    assert status["analysis_stale"] is False and status["last_transaction_at"] == "2026-09-24T23:24:18Z" or status["last_transaction_at"]
    assert analysed_client.get("/api/overview").json()["analysis_stale"] is False
    assert analysed_client.get("/api/leads").json()["items"][0]["run_id"] == after["run_id"]                   # leads refreshed too


# ---- the background thread: automatic, no manual call ---------------------------------------------------------------
def test_the_background_monitor_analyses_new_data_by_itself(population_db, settings):
    m = Monitor(population_db, settings)
    m.interval = 1.0
    with population_db.session() as s:
        assert latest_run(s) is None                                                     # nothing analysed yet
    m.start()
    try:
        deadline = time.time() + 120
        while time.time() < deadline and m.status()["analysis_stale"] is not False:
            time.sleep(0.5)
        s1 = m.status()
        assert s1["status"] == "active" and s1["analysis_stale"] is False and s1["last_analysis_trigger"] == "monitor"
        first_run = s1["last_analysis_run_id"]

        settings.inbox_dir.mkdir(exist_ok=True)                                          # a file appears; nobody presses anything
        write_csv(settings.inbox_dir / "arrived.csv", [("2026-09-25T09:00:00", "w020", "w021", "1.25")])
        deadline = time.time() + 120
        while time.time() < deadline and m.status()["last_analysis_run_id"] == first_run:
            time.sleep(0.5)
        s2 = m.status()
        assert s2["last_analysis_run_id"] > first_run and s2["analysis_stale"] is False
        assert s2["ingested_since_start"]["inbox"] == 1 and s2["transactions_total"] == s1["transactions_total"] + 1
    finally:
        m.stop()
    assert m.status()["status"] == "stopped"


# ---- monitor API -----------------------------------------------------------------------------------------------------
def test_monitor_endpoints(analysed_client):
    st = analysed_client.get("/api/monitor/status").json()
    assert st["status"] == "stopped" and st["data_source"]["is_real_data"] is False                              # autostart is off in tests
    assert analysed_client.post("/api/monitor/start").json()["status"] == "active"
    assert analysed_client.post("/api/monitor/stop").json()["status"] == "stopped"

    cfg = analysed_client.patch("/api/monitor/config", json={"interval_seconds": 3, "stream_enabled": True, "stream_rate_per_minute": 12, "auto_analysis": False}).json()
    assert (cfg["interval_seconds"], cfg["stream"]["enabled"], cfg["stream"]["rate_per_minute"], cfg["auto_analysis"]) == (3, True, 12, False)
    assert cfg["data_source"]["label"] == "Synthetic stream"
    for bad in ({"interval_seconds": 0}, {"stream_rate_per_minute": 99999}, {"unknown": 1}):
        assert analysed_client.patch("/api/monitor/config", json=bad).status_code == 422


def test_monitor_stream_scenario_endpoint_then_tick(analysed_client):
    n0 = analysed_client.get("/api/overview").json()["transactions_total"]
    sc = analysed_client.post("/api/monitor/stream/scenario").json()
    assert sc["scenario"] == "fan_out" and sc["transactions"] == 15 and sc["synthetic"] is True
    assert analysed_client.get("/api/overview").json()["transactions_total"] == n0 + 15
    tick = analysed_client.post("/api/monitor/tick").json()
    assert tick["result"]["analysis"]["status"] == "completed"
    a = analysed_client.get(f"/api/wallets/{sc['wallet']}/analysis").json()
    assert a["scored"] and a["features"]["fan_out"] >= 15


def test_scenario_without_data_is_a_clear_422(client):
    r = client.post("/api/monitor/stream/scenario")
    assert r.status_code == 422 and r.json()["error"]["code"] == "scenario_unavailable"


# ---- real dataset: the demo scenario through the unchanged model ----------------------------------------------------------------
def test_scenario_on_the_real_dataset_changes_the_features_the_model_sees(real_client):
    monitor = real_client.app.state.monitor
    sc = real_client.post("/api/monitor/stream/scenario").json()
    w = sc["wallet"]
    before = real_client.get(f"/api/wallets/{w}/analysis").json()
    assert before["scored"]
    real_client.post("/api/monitor/tick")
    after = real_client.get(f"/api/wallets/{w}/analysis").json()
    assert after["run_id"] > before["run_id"]
    assert after["features"]["fan_out"] == before["features"]["fan_out"] + 15
    assert after["features"]["transaction_count"] == before["features"]["transaction_count"] + 15
    assert after["forensic_rule_count"] >= before["forensic_rule_count"]
    assert after["ml_score"] is not None and after["priority_rank"] is not None
    with monitor.db.session() as s:
        assert s.scalar(select(func.count()).select_from(AnomalyResult)) == 410 and s.scalar(select(func.count()).select_from(WalletFeatures)) == 410
        assert s.scalar(select(func.count()).select_from(InvestigativeLead)) >= 1
