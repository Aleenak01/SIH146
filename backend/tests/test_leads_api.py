"""Analysis, leads and wallet-analysis endpoints."""

from __future__ import annotations

import pytest

from backend.models import FEATURE_COLUMNS


# ---- leads on the small population --------------------------------------------------------------------------
def test_leads_list_is_ranked_and_explained(analysed_client):
    body = analysed_client.get("/api/leads").json()
    assert body["total"] == len(body["items"]) > 0
    ranks = [l["priority_rank"] for l in body["items"]]
    assert ranks == sorted(ranks)
    for lead in body["items"]:
        assert lead["ml_prediction"] == "Anomalous" or lead["priority_level"] == "High"
        assert lead["reasons"] and any("Requires investigator review" in r for r in lead["reasons"])
        assert lead["is_case"] is False                      # a lead is not a case
        assert lead["source"] == "synthetic"
    assert "w000" in {l["wallet_address"] for l in body["items"]}


def test_lead_filters_sorting_and_paging(analysed_client):
    everything = analysed_client.get("/api/leads", params={"limit": 500}).json()["items"]
    anomalous = analysed_client.get("/api/leads", params={"ml_prediction": "Anomalous"}).json()
    assert anomalous["total"] == sum(l["ml_prediction"] == "Anomalous" for l in everything)
    high = analysed_client.get("/api/leads", params={"priority_level": "High"}).json()
    assert high["total"] == sum(l["priority_level"] == "High" for l in everything) > 0
    top = analysed_client.get("/api/leads", params={"sort": "combined_score", "order": "desc", "limit": 1}).json()["items"][0]
    assert top["combined_score"] == max(l["combined_score"] for l in everything)
    assert analysed_client.get("/api/leads", params={"q": "w000"}).json()["total"] == 1
    assert analysed_client.get("/api/leads", params={"min_score": 0.99}).json()["total"] == 0
    p1 = analysed_client.get("/api/leads", params={"limit": 2, "offset": 0}).json()
    p2 = analysed_client.get("/api/leads", params={"limit": 2, "offset": 2}).json()
    assert p1["total"] == p2["total"] and not {l["wallet_address"] for l in p1["items"]} & {l["wallet_address"] for l in p2["items"]}


@pytest.mark.parametrize("params", [{"priority_level": "Critical"}, {"ml_prediction": "Maybe"}, {"min_score": 2}, {"sort": "password"}, {"limit": 0}, {"limit": 501}])
def test_bad_lead_parameters_are_422(analysed_client, params):
    assert analysed_client.get("/api/leads", params=params).status_code == 422


def test_lead_detail_shows_the_evidence_behind_the_result(analysed_client):
    d = analysed_client.get("/api/leads/w000").json()
    assert d["wallet_address"] == "w000" and "not a case" in d["disclaimer"] and "not evidence of criminal activity" in d["disclaimer"]
    ev = d["contributing_evidence"]
    assert ev["ml"]["model"].startswith("Isolation Forest") and ev["ml"]["prediction"] == d["ml_prediction"]
    assert "not statistically validated" in ev["fusion"]["label"]
    assert ev["fusion"]["combined_score"] == pytest.approx(d["combined_score"])
    assert ev["fusion"]["combined_score"] == pytest.approx(0.4 * d["forensic_score"] + 0.6 * d["ml_score"], abs=1e-5)
    assert [f["rule_id"] for f in d["findings"]] == [r["rule_id"] for r in ev["forensic"]["rules"]] and len(d["findings"]) == d["forensic_rule_count"]
    assert set(d["features"]) == set(FEATURE_COLUMNS)
    rt = d["related_transactions"]
    assert rt["total"] == analysed_client.get("/api/wallets/w000").json()["transaction_count"] and len(rt["items"]) == 10
    rw = d["related_wallets"]
    assert rw and [w["transfers"] for w in rw] == sorted((w["transfers"] for w in rw), reverse=True)
    assert all(w["sent_to"] + w["received_from"] == w["transfers"] for w in rw)


def test_a_wallet_that_is_not_a_lead_has_an_analysis_but_no_lead(analysed_client):
    leads = {l["wallet_address"] for l in analysed_client.get("/api/leads", params={"limit": 500}).json()["items"]}
    other = next(w["address"] for w in analysed_client.get("/api/wallets", params={"limit": 100}).json()["items"] if w["address"] not in leads)
    r = analysed_client.get(f"/api/leads/{other}")
    assert r.status_code == 404 and "/analysis" in r.json()["error"]["message"]
    a = analysed_client.get(f"/api/wallets/{other}/analysis").json()
    assert a["scored"] and not a["is_lead"] and a["priority_rank"] and a["combined_score"] is not None and set(a["features"]) == set(FEATURE_COLUMNS)
    assert "not statistically validated" in a["fusion_note"]


def test_wallet_analysis_for_unscored_and_unknown_wallets(analysed_client):
    analysed_client.post("/api/transactions", json={"sender_wallet": "lonely", "receiver_wallet": "w001", "timestamp": "2026-09-01T00:00:00Z", "amount_btc": 0.3})
    analysed_client.post("/api/analysis/run")
    a = analysed_client.get("/api/wallets/lonely/analysis").json()
    assert a["scored"] is False and "fewer than 2 transactions" in a["unscored_reason"] and a["ml_score"] is None
    assert analysed_client.get("/api/wallets/nobody/analysis").status_code == 404


def test_wallet_analysis_before_any_run(client):
    client.post("/api/transactions", json={"sender_wallet": "a", "receiver_wallet": "b", "timestamp": "2026-01-01T00:00:00Z", "amount_btc": 1})
    a = client.get("/api/wallets/a/analysis").json()
    assert a["scored"] is False and "No analysis" in a["unscored_reason"]


# ---- analysis endpoints -------------------------------------------------------------------------------------------
def test_manual_run_and_run_history(analysed_client):
    r = analysed_client.post("/api/analysis/run")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "completed" and body["trigger"] == "manual" and body["new_leads"] == 0
    assert body["scored_wallets"] == 60 and body["duration_seconds"] > 0
    runs = analysed_client.get("/api/analysis/runs").json()
    assert [x["run_id"] for x in runs] == [2, 1] and runs[0]["run_config"]["fusion"]["ml_weight"] == 0.6
    latest = analysed_client.get("/api/analysis/latest").json()
    assert latest["run_id"] == 2 and latest["stale"] is False


def test_run_without_enough_data_is_a_clear_422(client):
    r = client.post("/api/analysis/run")
    assert r.status_code == 422 and r.json()["error"]["code"] == "analysis_unavailable"
    assert client.get("/api/analysis/latest").status_code == 404


def test_concurrent_run_is_a_409(analysed_client):
    from backend.analysis.service import ANALYSIS_LOCK

    assert ANALYSIS_LOCK.acquire(blocking=False)
    try:
        r = analysed_client.post("/api/analysis/run")
        assert r.status_code == 409 and r.json()["error"]["code"] == "analysis_in_progress"
    finally:
        ANALYSIS_LOCK.release()


def test_overview_reports_the_analysis_and_goes_stale_with_new_data(analysed_client):
    o = analysed_client.get("/api/overview").json()
    assert o["analysis_stale"] is False and o["leads_total"] == analysed_client.get("/api/leads").json()["total"]
    assert o["anomalous_wallets"] > 0 and o["last_analysis_at"]
    analysed_client.post("/api/transactions", json={"sender_wallet": "w001", "receiver_wallet": "w002", "timestamp": "2026-09-01T00:00:00Z", "amount_btc": 0.1})
    assert analysed_client.get("/api/overview").json()["analysis_stale"] is True


# ---- the project dataset through the API ----------------------------------------------------------------------------------
def test_real_dataset_leads(real_client):
    body = real_client.get("/api/leads", params={"limit": 500}).json()
    assert body["total"] == 42                                                     # 41 ML-flagged + 1 extra in the High band
    assert real_client.get("/api/leads", params={"ml_prediction": "Anomalous"}).json()["total"] == 41
    first = body["items"][0]
    assert (first["wallet_address"], first["priority_rank"], first["priority_level"]) == ("wallet_350", 1, "High")
    extra = [l for l in body["items"] if l["ml_prediction"] == "Normal"]
    assert [(l["wallet_address"], l["priority_level"]) for l in extra] == [("wallet_268", "High")]
    assert "Not flagged by the Isolation Forest" in extra[0]["reasons"][0]

    detail = real_client.get("/api/leads/wallet_350").json()
    assert detail["forensic_rule_count"] == 6 and detail["related_transactions"]["total"] == 100
    assert detail["features"]["fan_in"] == 79 and detail["features"]["transaction_count"] == 100
    assert {f["rule_id"] for f in detail["findings"]} == {"high_transaction_count", "fan_in", "many_counterparties", "repeated_relationships", "high_received_volume", "incoming_outgoing_imbalance"}
    ov = real_client.get("/api/overview").json()
    assert (ov["anomalous_wallets"], ov["leads_total"], ov["analysis_stale"]) == (41, 42, False)
