"""Bulk analysis endpoint (frontend data source) and persisted monitor settings."""

from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.models import FEATURE_COLUMNS


# ---- bulk analysis --------------------------------------------------------------------------------------------
def test_bulk_analysis_before_any_analysis_is_empty(loaded_client):
    body = loaded_client.get("/api/analysis/wallets").json()
    assert body["run_id"] is None and body["wallets"] == [] and "not statistically validated" in body["fusion_note"]


def test_bulk_analysis_matches_the_leads_and_the_wallet_endpoint(analysed_client):
    body = analysed_client.get("/api/analysis/wallets").json()
    wallets = body["wallets"]
    assert body["scored_wallets"] == len(wallets) > 0 and [w["priority_rank"] for w in wallets] == list(range(1, len(wallets) + 1))
    assert (body["forensic_weight"], body["ml_weight"]) == (0.4, 0.6)
    leads = analysed_client.get("/api/leads", params={"limit": 500}).json()["items"]
    assert {w["wallet_address"] for w in wallets if w["is_lead"]} == {l["wallet_address"] for l in leads}
    for w in wallets:
        assert set(w["features"]) == set(FEATURE_COLUMNS) and len(w["triggered_rules"]) == len(w["findings"]) == w["forensic_rule_count"]
        assert w["is_lead"] == bool(w["reasons"])
    one = wallets[3]
    single = analysed_client.get(f"/api/wallets/{one['wallet_address']}/analysis").json()
    for key in ("ml_score", "ml_prediction", "combined_score", "priority_rank", "priority_level", "forensic_rule_count", "evidence_level", "review_status"):
        assert single[key] == one[key]
    assert single["features"] == one["features"] and [f["rule_id"] for f in single["findings"]] == one["triggered_rules"]


def test_bulk_analysis_shows_review_state_and_cases(analysed_client):
    top = analysed_client.get("/api/analysis/wallets").json()["wallets"][0]["wallet_address"]
    analysed_client.put(f"/api/wallets/{top}/review", json={"status": "Under Review"})
    row = analysed_client.get("/api/analysis/wallets").json()["wallets"][0]
    assert row["review_status"] == "Under Review" and row["case_ids"] == []
    case = analysed_client.post("/api/cases", json={"title": "t", "items": [{"type": "lead", "id": top}]}).json()["case_id"]
    row = analysed_client.get("/api/analysis/wallets").json()["wallets"][0]
    assert row["review_status"] == "Case Created" and row["case_ids"] == [case]


def test_bulk_analysis_reproduces_the_cli_outputs_for_the_project_dataset(real_client, project_root):
    body = real_client.get("/api/analysis/wallets").json()
    assert body["scored_wallets"] == 410 and body["unscored_wallets"] == 0
    got = {w["wallet_address"]: w for w in body["wallets"]}
    with open(project_root / "data" / "fusion_results.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            w = got[row["wallet_address"]]
            assert w["ml_prediction"] == row["ml_anomaly_prediction"] and w["forensic_rule_count"] == int(row["forensic_rule_count"])
            assert w["ml_score"] == pytest.approx(float(row["ml_anomaly_score"]), abs=1e-6)
            assert w["combined_score"] == pytest.approx(float(row["combined_score"]), abs=1e-6)
            assert (";".join(w["triggered_rules"]) or "none") == row["forensic_triggered_rules"]
    assert sum(w["is_lead"] for w in body["wallets"]) == 42


# ---- persisted settings ---------------------------------------------------------------------------------------
def test_settings_read_defaults_and_state_that_the_data_is_synthetic(client):
    body = client.get("/api/settings").json()
    assert set(body["settings"]) == {"monitor_enabled", "interval_seconds", "auto_analysis", "stream_enabled", "stream_rate_per_minute"}
    assert body["saved_keys"] == [] and body["data_source"]["is_real_data"] is False and body["data_source"]["mode"] == "synthetic"
    assert body["data_source"]["real_bitcoin"]["available"] is False and "secret" not in str(body).replace("secrets_note", "")


def test_settings_update_applies_and_persists(client):
    out = client.put("/api/settings", json={"interval_seconds": 7, "auto_analysis": False}).json()
    assert out["settings"]["interval_seconds"] == 7 and out["settings"]["auto_analysis"] is False
    assert out["saved_keys"] == ["auto_analysis", "interval_seconds"]
    assert client.get("/api/monitor/status").json()["interval_seconds"] == 7
    out = client.put("/api/settings", json={"monitor_enabled": True}).json()
    assert out["settings"]["monitor_enabled"] is True and client.get("/api/monitor/status").json()["status"] == "active"
    assert client.put("/api/settings", json={"monitor_enabled": False}).json()["settings"]["monitor_enabled"] is False


@pytest.mark.parametrize("body", [{"interval_seconds": 0}, {"interval_seconds": 99999}, {"stream_rate_per_minute": 0}, {"api_key": "x"}, {"monitor_enabled": "maybe"}])
def test_invalid_settings_are_rejected_and_change_nothing(client, body):
    before = client.get("/api/settings").json()
    r = client.put("/api/settings", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] in ("invalid_configuration", "validation_error")
    assert client.get("/api/settings").json() == before


def test_saved_settings_survive_a_restart(settings):
    with TestClient(create_app(settings)) as first:
        first.put("/api/settings", json={"interval_seconds": 12, "auto_analysis": False, "monitor_enabled": True})
    with TestClient(create_app(settings)) as second:
        body = second.get("/api/settings").json()
        assert body["settings"]["interval_seconds"] == 12 and body["settings"]["auto_analysis"] is False and body["settings"]["monitor_enabled"] is True
        assert second.get("/api/monitor/status").json()["status"] == "active"
    with TestClient(create_app(settings)) as third:                 # the last run stopped it again on shutdown, but the choice is saved
        third.put("/api/settings", json={"monitor_enabled": False})
    with TestClient(create_app(settings)) as fourth:
        assert fourth.get("/api/settings").json()["settings"]["monitor_enabled"] is False
