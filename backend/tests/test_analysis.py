"""The analysis pipeline through the backend: equivalence with the CLI, forensic findings, unscored wallets, failures."""

from __future__ import annotations

import csv
from collections import Counter

import pytest
from sqlalchemy import func, select

from backend.analysis import ml_bridge
from backend.analysis.ml_bridge import AnalysisError
from backend.analysis.service import ANALYSIS_LOCK, AnalysisBusy, analysis_is_stale, latest_run, run_analysis
from backend.models import (
    FEATURE_COLUMNS, AnalysisRun, AnomalyResult, ForensicFinding, FusionResult, InvestigativeLead, Transaction, WalletFeatures,
)
from backend.services.ingest import ingest_payloads


# ---- the project dataset: the backend must reproduce the CLI pipeline exactly ------------------------------
def _csv_rows(project_root, name):
    return {r["wallet_address"]: r for r in csv.DictReader((project_root / "data" / name).open(encoding="utf-8"))}


def test_backend_reproduces_the_cli_pipeline_exactly(real_db, project_root):
    features, anomaly, forensic, fusion = (_csv_rows(project_root, n) for n in (
        "wallet_behavior_features.csv", "anomaly_results.csv", "forensic_results.csv", "fusion_results.csv"))
    with real_db.session() as s:
        wf = {r.wallet_address: r for r in s.scalars(select(WalletFeatures))}
        ar = {r.wallet_address: r for r in s.scalars(select(AnomalyResult))}
        fr = {r.wallet_address: r for r in s.scalars(select(FusionResult))}
        rules: dict[str, list[str]] = {}
        for f in s.scalars(select(ForensicFinding).order_by(ForensicFinding.finding_id)):
            rules.setdefault(f.wallet_address, []).append(f.rule_id)

    assert set(wf) == set(ar) == set(fr) == set(features) and len(features) == 410
    for w in features:
        for c in FEATURE_COLUMNS:
            assert getattr(wf[w], c) == pytest.approx(float(features[w][c]), abs=1e-9), (w, c)
        assert ar[w].anomaly_score == pytest.approx(float(anomaly[w]["anomaly_score"]), abs=1e-6), w
        assert ar[w].anomaly_prediction == anomaly[w]["anomaly_prediction"], w
        expected_rules = [] if forensic[w]["triggered_rules"] == "none" else forensic[w]["triggered_rules"].split(";")
        assert rules.get(w, []) == expected_rules, w
        assert fr[w].forensic_rule_count == int(forensic[w]["rule_count"]) and fr[w].evidence_level == forensic[w]["evidence_level"], w
        assert fr[w].combined_score == pytest.approx(float(fusion[w]["combined_score"]), abs=1e-6), w
    assert sum(r.anomaly_prediction == "Anomalous" for r in ar.values()) == 41

    # priority rank equals the ordering the frontend uses (combined score descending, then wallet address)
    order = sorted(fusion, key=lambda w: (-float(fusion[w]["combined_score"]), w))
    assert [fr[w].priority_rank for w in order] == list(range(1, 411))
    assert Counter(r.priority_level for r in fr.values()) == {"High": 21, "Medium": 61, "Low": 328}


def test_run_record_documents_the_model_and_the_prototype_weights(real_db):
    with real_db.session() as s:
        run = latest_run(s)
        assert (run.status, run.transfer_count, run.wallet_count, run.trigger) == ("completed", 5000, 410, "manual")
        cfg = run.model_config_json
    assert cfg["isolation_forest"] == {"n_estimators": 200, "contamination": 0.1, "random_state": 42, "unsupervised": True}
    assert (cfg["fusion"]["forensic_weight"], cfg["fusion"]["ml_weight"]) == (0.4, 0.6)
    assert "not statistically validated" in cfg["fusion"]["note"]
    assert cfg["scored_wallets"] == 410 and cfg["unscored_wallets"] == 0
    with real_db.session() as s:
        assert not analysis_is_stale(s)


def test_the_ml_directory_is_used_unmodified(project_root):
    """The bridge imports the existing modules; it does not carry its own copy of the model."""
    fe, ad, fr, rf = ml_bridge.load_ml()
    for module in (fe, ad, fr, rf):
        assert module.__file__.startswith(str(project_root / "ml"))
    assert (ad.N_ESTIMATORS, ad.CONTAMINATION, ad.RANDOM_STATE) == (200, 0.10, 42)


# ---- small populations --------------------------------------------------------------------------------------
def test_forensic_findings_carry_rule_value_threshold_and_the_rules_own_sentence(analysed_db):
    with analysed_db.session() as s:
        hub_findings = list(s.scalars(select(ForensicFinding).where(ForensicFinding.wallet_address == "w000")))
    by_rule = {f.rule_id: f for f in hub_findings}
    assert "fan_in" in by_rule
    f = by_rule["fan_in"]
    assert f.rule_name == "High fan-in" and f.severity is None                       # the existing rules define no severity
    assert f.feature == "fan_in" and f.feature_value > f.threshold
    assert "fan_in=" in f.evidence and "90th percentile" in f.evidence


def test_leads_include_the_hub_and_are_ranked(analysed_db):
    with analysed_db.session() as s:
        leads = list(s.scalars(select(InvestigativeLead).order_by(InvestigativeLead.priority_rank)))
    assert leads and "w000" in {l.wallet_address for l in leads}
    assert all(l.ml_prediction == "Anomalous" or l.priority_level == "High" for l in leads)
    assert [l.priority_rank for l in leads] == sorted(l.priority_rank for l in leads)
    assert all(l.contributing_evidence["fusion"]["label"].startswith("Prototype fusion weighting") for l in leads)


def test_wallets_with_a_single_transaction_are_left_unscored_not_invented(analysed_db):
    with analysed_db.transaction() as s:
        ingest_payloads(s, [{"sender_wallet": "lonely", "receiver_wallet": "w001", "timestamp": "2026-08-01T00:00:00", "amount_btc": 0.3}])
    run = run_analysis(analysed_db)
    assert run.unscored_wallets == 1 and run.scored_wallets == 60 and run.wallet_count == 61
    with analysed_db.session() as s:
        assert s.get(AnomalyResult, (run.run_id, "lonely")) is None and s.get(WalletFeatures, (run.run_id, "lonely")) is None
        # nothing stored is missing a value
        for col in FEATURE_COLUMNS:
            assert s.scalar(select(func.count()).select_from(WalletFeatures).where(getattr(WalletFeatures, col).is_(None))) == 0
        assert "lonely" in latest_run(s).model_config_json["unscored_examples"]


def test_too_little_data_is_refused_and_recorded(db):
    with pytest.raises(AnalysisError, match="no transactions"):
        run_analysis(db)
    with db.transaction() as s:
        ingest_payloads(s, [{"sender_wallet": f"a{i}", "receiver_wallet": f"a{i + 1}", "timestamp": f"2026-01-0{i + 1}T00:00:00", "amount_btc": 1} for i in range(4)])
    with pytest.raises(AnalysisError, match="at least"):
        run_analysis(db)
    with db.session() as s:
        runs = list(s.scalars(select(AnalysisRun)))
        assert [r.status for r in runs] == ["failed", "failed"] and all(r.error for r in runs)
        assert s.scalar(select(func.count()).select_from(AnomalyResult)) == 0
        assert latest_run(s) is None


def test_rerunning_replaces_detail_rows_but_keeps_run_history(analysed_db):
    first = latest_run_id(analysed_db)
    second = run_analysis(analysed_db).run_id
    assert second > first
    with analysed_db.session() as s:
        assert set(s.scalars(select(WalletFeatures.run_id).distinct())) == {second}
        assert set(s.scalars(select(AnomalyResult.run_id).distinct())) == {second}
        assert set(s.scalars(select(FusionResult.run_id).distinct())) == {second}
        assert s.scalar(select(func.count()).select_from(AnalysisRun)) == 2
        assert set(s.scalars(select(InvestigativeLead.run_id))) == {second}


def test_a_failed_run_keeps_the_previous_results(analysed_db, monkeypatch):
    good = latest_run_id(analysed_db)
    with analysed_db.session() as s:
        before = s.scalar(select(func.count()).select_from(InvestigativeLead))

    def boom(_transfers):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("backend.analysis.service.run_pipeline", boom)
    with pytest.raises(RuntimeError):
        run_analysis(analysed_db)
    with analysed_db.session() as s:
        assert latest_run(s).run_id == good
        assert s.scalar(select(func.count()).select_from(InvestigativeLead)) == before
        failed = s.scalars(select(AnalysisRun).where(AnalysisRun.status == "failed")).one()
        assert "model exploded" in failed.error


def test_only_one_analysis_runs_at_a_time(analysed_db):
    assert ANALYSIS_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(AnalysisBusy):
            run_analysis(analysed_db)
    finally:
        ANALYSIS_LOCK.release()
    run_analysis(analysed_db)                          # and it works again afterwards


def test_staleness_follows_the_data(analysed_db):
    with analysed_db.session() as s:
        assert not analysis_is_stale(s)
    with analysed_db.transaction() as s:
        ingest_payloads(s, [{"sender_wallet": "w001", "receiver_wallet": "w002", "timestamp": "2026-09-01T00:00:00", "amount_btc": 0.1}])
    with analysed_db.session() as s:
        assert analysis_is_stale(s)


def test_analysis_is_scoped_to_one_source(analysed_db):
    """Synthetic analysis never reads real_bitcoin transactions (populations are not mixed)."""
    from backend.ingestion.base import SourcedTransaction
    from backend.schemas import TransactionIn
    from backend.services.ingest import ingest

    with analysed_db.session() as s:
        synthetic_transfers = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == "synthetic"))
    with analysed_db.transaction() as s:
        ingest(s, [SourcedTransaction(TransactionIn(transaction_id=f"tx{i}", sender_wallet=f"r{i}", receiver_wallet=f"r{i + 1}", timestamp="2026-05-01T00:00:00", amount_btc=1))
                   for i in range(30)], "real_bitcoin")
    summary = run_analysis(analysed_db, "synthetic")
    assert (summary.transfer_count, summary.wallet_count) == (synthetic_transfers, 60)     # the 30 real transfers are not counted
    with analysed_db.session() as s:
        scored = set(s.scalars(select(AnomalyResult.wallet_address)))
    assert not any(w.startswith("r") for w in scored)


def latest_run_id(db) -> int:
    with db.session() as s:
        return latest_run(s).run_id
