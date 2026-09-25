"""SYNTHETIC network observations: generation, safety, import, live hook, API."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.ingestion import synthetic_network as sn
from backend.ingestion.base import SourcedTransaction
from backend.models import NetworkObservation, Transaction
from backend.schemas import TransactionIn
from backend.services import network as ns
from backend.services.ingest import ingest


def fake_transactions(n=400, wallets=40, seed=1):
    import random

    rng = random.Random(seed)
    names = [f"w{i:02d}" for i in range(wallets)]
    t = datetime(2026, 1, 1)
    out = []
    for i in range(1, n + 1):
        t += timedelta(minutes=rng.randint(1, 300))
        a, b = rng.sample(names, 2)
        out.append((f"syn-{i:06d}", t, a, b))
    return out


# ---- generator ---------------------------------------------------------------------------------------------
def test_generation_is_deterministic_and_seed_dependent():
    txs = fake_transactions()
    a = sn.generate_dataset(txs, seed=5)
    assert a == sn.generate_dataset(txs, seed=5)
    assert a != sn.generate_dataset(txs, seed=6)


def test_only_documentation_ip_ranges_are_ever_used():
    rows = sn.generate_dataset(fake_transactions(), seed=3)
    assert rows and all(sn.is_documentation_ip(r["ip_address"]) for r in rows)
    assert len(sn.IP_POOL) == 3 * 254
    for real in ("8.8.8.8", "1.1.1.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "203.0.114.1", "198.51.101.5"):
        assert not sn.is_documentation_ip(real)


def test_observation_shape_and_labels():
    txs = fake_transactions()
    by_id = {t[0]: t for t in txs}
    rows = sn.generate_dataset(txs, seed=3)
    for r in rows:
        tid, ts, sender, receiver = by_id[r["transaction_id"]]
        role = r["observation_id"][-1]
        assert r["observation_id"] == f"obs-{tid}-{role}" and role in "SR"
        assert r["wallet_address"] == (sender if role == "S" else receiver)
        assert ts < r["observed_at"] <= ts + timedelta(seconds=31)
        assert r["origin"] == "synthetic_network_observation" and r["is_synthetic"] is True
        assert r["geo_region"].startswith("SYN-REGION-") and r["device_id"].startswith("dev-") and r["session_id"].startswith("sess-dev-")


def test_coverage_and_sharing_on_a_realistic_sized_population():
    txs = fake_transactions(n=3000, wallets=200, seed=2)
    rows = sn.generate_dataset(txs, seed=sn.DEFAULT_SEED)
    sender = sum(r["observation_id"].endswith("-S") for r in rows) / len(txs)
    receiver = sum(r["observation_id"].endswith("-R") for r in rows) / len(txs)
    assert 0.75 < sender < 0.85 and 0.45 < receiver < 0.55
    dev_wallets = defaultdict(set)
    for r in rows:
        dev_wallets[r["device_id"]].add(r["wallet_address"])
    shared = [ws for ws in dev_wallets.values() if len(ws) > 1]
    assert shared and all(2 <= len(ws) <= 5 for ws in shared)
    assert 0.08 < sum(len(ws) for ws in shared) / 200 < 0.22                     # about 15% of wallets share a device
    wallet_devices = defaultdict(set)
    for r in rows:
        wallet_devices[r["wallet_address"]].add(r["device_id"])
    assert all(len(d) == 1 for d in wallet_devices.values())                       # one device per wallet
    ip_devices = defaultdict(set)
    for r in rows:
        ip_devices[r["ip_address"]].add(r["device_id"])
    assert any(len(d) > 1 for d in ip_devices.values())                            # some IPs are shared across devices


def test_sessions_group_observations_of_a_device_within_two_hours():
    rows = sn.generate_dataset(fake_transactions(n=3000, wallets=30, seed=4), seed=9)
    by_dev = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["observed_at"]):
        by_dev[r["device_id"]].append(r)
    for obs in by_dev.values():
        for prev, cur in zip(obs, obs[1:]):
            same = cur["session_id"] == prev["session_id"]
            assert same == (cur["observed_at"] - prev["observed_at"] <= sn.SESSION_GAP) or cur["observed_at"] < prev["observed_at"]
    assert any(len({o["session_id"] for o in obs}) > 1 for obs in by_dev.values())


# ---- database: CSV round trip and safety checks -------------------------------------------------------------------
def test_csv_roundtrip_is_idempotent_and_replaceable(population_db, tmp_path):
    path = tmp_path / "o.csv"
    n = ns.generate_csv(population_db, path)
    assert n == len(list(csv.DictReader(path.open()))) > 0
    r1 = ns.import_csv(population_db, path, replace=True)
    r2 = ns.import_csv(population_db, path)
    assert (r1.inserted, r1.rejected) == (n, []) and (r2.inserted, r2.skipped_existing) == (0, n)
    with population_db.session() as s:
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) == n
    r3 = ns.import_csv(population_db, path, replace=True)
    assert r3.inserted == n


def _edit_csv(path, mutate):
    rows = list(csv.DictReader(path.open()))
    mutate(rows)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sn.CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def test_import_refuses_unsafe_or_inconsistent_rows(population_db, tmp_path):
    path = tmp_path / "o.csv"
    ns.generate_csv(population_db, path)

    def mutate(rows):
        rows[0]["ip_address"] = "8.8.8.8"                                   # a real-looking public address
        rows[1]["transaction_id"] = "syn-999999"                            # no such transaction
        rows[2]["wallet_address"] = "not_a_party"
        rows[3]["is_synthetic"] = "false"
        rows[4]["ip_address"] = "192.168.1.5"                               # private, not documentation

    _edit_csv(path, mutate)
    r = ns.import_csv(population_db, path, replace=True)
    reasons = " | ".join(x["error"] for x in r.rejected)
    assert len(r.rejected) == 5
    assert "not in a documentation range" in reasons and "does not exist" in reasons and "not a party" in reasons and "not marked synthetic" in reasons
    with population_db.session() as s:
        assert not s.scalars(select(NetworkObservation).where(NetworkObservation.ip_address.in_(["8.8.8.8", "192.168.1.5"]))).all()


def test_wrong_columns_are_refused(population_db, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Unexpected columns"):
        ns.import_csv(population_db, path)


def test_observations_cannot_be_attached_to_real_bitcoin_transactions(db, tmp_path):
    with db.transaction() as s:
        ingest(s, [SourcedTransaction(TransactionIn(transaction_id=f"tx{i}", sender_wallet=f"r{i}", receiver_wallet=f"r{i + 1}", timestamp="2026-05-01T00:00:00", amount_btc=1))
                   for i in range(5)], "real_bitcoin")
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) == 0          # nothing generated for real data
    path = tmp_path / "o.csv"
    sn.write_csv(path, [{"observation_id": "obs-tx1-S", "transaction_id": "tx1", "wallet_address": "r1", "ip_address": "192.0.2.1", "device_id": "dev-1",
                         "user_agent": "x", "network_type": "wifi", "session_id": "s", "geo_region": "SYN-REGION-01", "observed_at": datetime(2026, 5, 1), "origin": sn.ORIGIN, "is_synthetic": True}])
    r = ns.import_csv(db, path)
    assert r.inserted == 0 and "only be attached to synthetic" in r.rejected[0]["error"]


def test_replacing_synthetic_transactions_also_removes_their_observations(db, settings):
    from backend.services.importer import import_synthetic_csv

    import_synthetic_csv(db, settings.dataset_csv)
    ns.generate_csv(db, settings.network_csv)
    ns.import_csv(db, settings.network_csv)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) > 0
    import_synthetic_csv(db, settings.dataset_csv, replace=True)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) == 0


# ---- live hook ------------------------------------------------------------------------------------------------------
def _device_of(db, wallet):
    with db.session() as s:
        return s.scalar(select(NetworkObservation.device_id).where(NetworkObservation.wallet_address == wallet).order_by(NetworkObservation.observed_at.desc()).limit(1))


def _post_until_observed(db, sender, receiver, n=40):
    """Ingest transactions until the sender's side got an observation (coverage is 0.8, so a few tries at most)."""
    for i in range(n):
        with db.transaction() as s:
            out = ingest(s, [SourcedTransaction(TransactionIn(sender_wallet=sender, receiver_wallet=receiver, timestamp=f"2026-09-2{i % 5}T1{i % 10}:00:00", amount_btc=0.1 + i))], "synthetic")
        with db.session() as s:
            if s.scalar(select(NetworkObservation.observation_id).where(NetworkObservation.observation_id == f"obs-{out.transaction_ids[0]}-S")):
                return out.transaction_ids[0]
    raise AssertionError("no observation created")


def test_new_transactions_continue_the_wallets_existing_device(networked_db):
    wallet = "w010"
    before = _device_of(networked_db, wallet)
    assert before
    tid = _post_until_observed(networked_db, wallet, "w011")
    with networked_db.session() as s:
        o = s.get(NetworkObservation, f"obs-{tid}-S")
    assert o.device_id == before and o.is_synthetic and o.origin == "synthetic_network_observation" and sn.is_documentation_ip(o.ip_address)


def test_a_new_wallet_gets_a_new_device_and_numbering_continues(networked_db):
    with networked_db.session() as s:
        highest = max(int(d[4:]) for d in s.scalars(select(NetworkObservation.device_id).distinct()))
    tid = _post_until_observed(networked_db, "brand_new_wallet", "w001")
    with networked_db.session() as s:
        o = s.get(NetworkObservation, f"obs-{tid}-S")
    assert o.device_id == f"dev-{highest + 1:04d}" and sn.is_documentation_ip(o.ip_address)


def test_the_live_hook_is_deterministic(tmp_path):
    from backend.database import Database
    from backend.tests.conftest import seed_population

    results = []
    for k in range(2):
        d = Database(f"sqlite:///{(tmp_path / f'd{k}.db').as_posix()}")
        d.init_db()
        seed_population(d, n_wallets=25, n_transfers=150, seed=11)
        with d.session() as s:
            results.append([(o.observation_id, o.device_id, o.ip_address, o.session_id) for o in s.scalars(select(NetworkObservation).order_by(NetworkObservation.observation_id))])
        d.dispose()
    assert results[0] == results[1] and results[0]


def test_ingest_can_skip_observations(db):
    with db.transaction() as s:
        ingest(s, [SourcedTransaction(TransactionIn(sender_wallet="a", receiver_wallet="b", timestamp="2026-01-01T00:00:00", amount_btc=1))], "synthetic", observe=False)
        assert s.scalar(select(func.count()).select_from(NetworkObservation)) == 0


# ---- API ------------------------------------------------------------------------------------------------------------------
def test_import_endpoint_flow(loaded_client, settings):
    r = loaded_client.post("/api/import/synthetic-network", json={"generate": True})
    body = r.json()
    assert r.status_code == 200 and body["generated"] and body["inserted"] > 0 and body["rejected"] == [] and "Synthetic network observation" in body["note"]
    assert settings.network_csv.is_file()
    again = loaded_client.post("/api/import/synthetic-network", json={}).json()
    assert again["inserted"] == 0 and again["skipped_existing"] == body["inserted"] and again["generated"] is False
    ov = loaded_client.get("/api/overview").json()
    assert ov["network_observations_total"] == body["observations_total"]


def test_import_endpoint_needs_transactions_first(client):
    r = client.post("/api/import/synthetic-network", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transactions"


def test_import_endpoint_with_a_bad_file(loaded_client, settings):
    settings.network_csv.write_text("x,y\n1,2\n")
    r = loaded_client.post("/api/import/synthetic-network", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_dataset"
    assert loaded_client.post("/api/import/synthetic-network", json={"nope": 1}).status_code == 422


def test_observation_endpoints(networked_client):
    page = networked_client.get("/api/network/observations", params={"limit": 5}).json()
    assert page["total"] > 5 and len(page["items"]) == 5 and "not derived from the Bitcoin blockchain" in page["note"]
    o = page["items"][0]
    assert o["is_synthetic"] and o["label"] == "Synthetic network observation" and o["observed_party"] in ("sender", "receiver")
    assert networked_client.get(f"/api/network/observations/{o['observation_id']}").json()["observation_id"] == o["observation_id"]
    assert networked_client.get("/api/network/observations/nope").status_code == 404

    by_w = networked_client.get("/api/network/observations", params={"wallet": o["wallet_address"]}).json()
    assert by_w["total"] >= 1 and all(x["wallet_address"] == o["wallet_address"] for x in by_w["items"])
    by_ip = networked_client.get("/api/network/observations", params={"ip": o["ip_address"]}).json()
    assert by_ip["total"] >= 1 and all(x["ip_address"] == o["ip_address"] for x in by_ip["items"])
    by_tx = networked_client.get("/api/network/observations", params={"transaction_id": o["transaction_id"]}).json()
    assert 1 <= by_tx["total"] <= 2
    assert networked_client.get("/api/network/observations", params={"limit": 0}).status_code == 422


def test_entity_endpoint(networked_client):
    items = networked_client.get("/api/network/observations", params={"limit": 500}).json()["items"]
    shared = Counter(i["device_id"] for i in items)
    o = items[0]
    dev = networked_client.get(f"/api/network/entities/device/{o['device_id']}").json()
    assert dev["observation_count"] == sum(1 for i in networked_client.get("/api/network/observations", params={"device": o['device_id'], "limit": 500}).json()["items"]) and o["wallet_address"] in dev["wallets"]
    assert o["ip_address"] in dev["related"]["ips"] and "Synthetic network observation" in dev["note"]
    ip = networked_client.get(f"/api/network/entities/ip/{o['ip_address']}").json()
    assert o["device_id"] in ip["related"]["devices"]
    sess = networked_client.get(f"/api/network/entities/session/{o['session_id']}").json()
    assert sess["observation_count"] >= 1
    assert networked_client.get("/api/network/entities/device/dev-9999").status_code == 404
    assert networked_client.get("/api/network/entities/carrier/x").status_code == 422
    assert shared


def test_the_project_dataset_gets_a_plausible_synthetic_network(real_db):
    with real_db.session() as s:
        obs = list(s.scalars(select(NetworkObservation)))
        n_tx = s.scalar(select(func.count()).select_from(Transaction))
    assert len(obs) > 5000 and all(o.is_synthetic and sn.is_documentation_ip(o.ip_address) for o in obs)
    assert 0.75 < sum(o.observation_id.endswith("-S") for o in obs) / n_tx < 0.85
    dev_w = defaultdict(set)
    for o in obs:
        dev_w[o.device_id].add(o.wallet_address)
    assert sum(len(w) > 1 for w in dev_w.values()) >= 10
