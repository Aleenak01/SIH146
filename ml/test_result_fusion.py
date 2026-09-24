"""
test_result_fusion.py
----------------------
Scenario tests for ml/result_fusion.py's boundary validation + fusion logic (Step 5).

These call fuse_wallet() / prepare_source() directly with synthetic per-wallet records, independent
of the real data/forensic_results.csv and data/anomaly_results.csv files, so invalid/missing/
malformed inputs can be exercised without touching real pipeline output.

Run from the project root:
    .\\.venv\\Scripts\\python.exe ml\\test_result_fusion.py
"""

import pandas as pd

import result_fusion as rf

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def forensic_row(rule_count=3, evidence_level="Multiple behavioural indicators",
                  triggered_rules="high_transaction_count;fan_out", explanation="example finding"):
    return {"rule_count": rule_count, "evidence_level": evidence_level,
            "triggered_rules": triggered_rules, "explanation": explanation}


def ml_row(anomaly_score=0.85, anomaly_prediction="Anomalous"):
    return {"anomaly_score": anomaly_score, "anomaly_prediction": anomaly_prediction}


def test_1_normal_valid():
    print("\nTest 1: normal transaction, valid forensic + ML outputs")
    r = rf.fuse_wallet("W1", forensic_row(rule_count=3), ml_row(anomaly_score=0.85))
    check("valid == True", r["valid"] is True)
    check("combined_score is set", r["combined_score"] is not None)
    expected = round(rf.FORENSIC_WEIGHT * (3 / rf.TOTAL_FORENSIC_RULES) + rf.ML_WEIGHT * 0.85, 6)
    check("combined_score matches weighted formula", r["combined_score"] == expected,
          f"got {r['combined_score']}, expected {expected}")
    check("no errors", r["errors"] == "", r["errors"])


def test_2_strong_forensic_weak_ml():
    print("\nTest 2: strong forensic signal, weak ML anomaly score")
    r = rf.fuse_wallet("W2", forensic_row(rule_count=9), ml_row(anomaly_score=0.10))
    check("valid == True", r["valid"] is True)
    check("forensic_score > ml_anomaly_score", r["forensic_score"] > r["ml_anomaly_score"],
          f"forensic={r['forensic_score']} ml={r['ml_anomaly_score']}")
    check("combined_score within [0,1]", 0.0 <= r["combined_score"] <= 1.0)


def test_3_weak_forensic_strong_ml():
    print("\nTest 3: weak forensic signal, strong ML anomaly score")
    r = rf.fuse_wallet("W3", forensic_row(rule_count=0, triggered_rules="none"), ml_row(anomaly_score=0.95))
    check("valid == True", r["valid"] is True)
    check("forensic_score == 0.0", r["forensic_score"] == 0.0, str(r["forensic_score"]))
    check("ml_anomaly_score > forensic_score", r["ml_anomaly_score"] > r["forensic_score"])


def test_4_both_strong():
    print("\nTest 4: both forensic and ML signals strong")
    r = rf.fuse_wallet("W4", forensic_row(rule_count=10), ml_row(anomaly_score=0.95))
    check("valid == True", r["valid"] is True)
    check("combined_score is high (>0.8)", r["combined_score"] > 0.8, str(r["combined_score"]))
    check("combined_score within [0,1]", 0.0 <= r["combined_score"] <= 1.0)


def test_5_missing_ml_score():
    print("\nTest 5: missing ML score (wallet absent from ML source)")
    r = rf.fuse_wallet("W5", forensic_row(rule_count=7), None)
    check("valid == False", r["valid"] is False)
    check("combined_score is None (not defaulted to 0)", r["combined_score"] is None)
    check("ml_anomaly_score is None", r["ml_anomaly_score"] is None)
    check("forensic_score still preserved", r["forensic_score"] is not None)
    check("errors mention ML missing", "ML result missing" in r["errors"], r["errors"])


def test_6_invalid_ml_score():
    print("\nTest 6: ML score outside the expected 0-1 range")
    r = rf.fuse_wallet("W6", forensic_row(rule_count=7), ml_row(anomaly_score=1.7))
    check("valid == False", r["valid"] is False)
    check("combined_score is None (invalid score not used in calculation)", r["combined_score"] is None)
    check("ml_anomaly_score rejected, not clipped", r["ml_anomaly_score"] is None)
    check("errors mention out-of-range score", "out of expected [0, 1] range" in r["errors"], r["errors"])


def test_7_missing_forensic_score():
    print("\nTest 7: missing forensic score (wallet absent from forensic source)")
    r = rf.fuse_wallet("W7", None, ml_row(anomaly_score=0.6))
    check("valid == False", r["valid"] is False)
    check("combined_score is None", r["combined_score"] is None)
    check("forensic_score is None", r["forensic_score"] is None)
    check("ml_anomaly_score still preserved", r["ml_anomaly_score"] is not None)
    check("errors mention forensic missing", "forensic result missing" in r["errors"], r["errors"])


def test_8_missing_evidence():
    print("\nTest 8: forensic score exists but triggered-rule evidence is missing")
    r = rf.fuse_wallet("W8", forensic_row(rule_count=3, triggered_rules=None), ml_row(anomaly_score=0.5))
    check("valid == True (score usable despite missing evidence)", r["valid"] is True)
    check("forensic_score preserved", r["forensic_score"] == round(3 / rf.TOTAL_FORENSIC_RULES, 6))
    check("warning about missing evidence, not an invented rule", "triggered_rules evidence is missing" in r["warnings"], r["warnings"])
    check("triggered_rules not invented (still None)", r["forensic_triggered_rules"] is None)


def test_9_duplicate_wallets():
    print("\nTest 9 (extra): duplicate wallet rows in a source are treated as unavailable, not arbitrarily picked")
    df = pd.DataFrame([
        {"wallet_address": "DUP1", "rule_count": 2, "evidence_level": "x", "triggered_rules": "a", "explanation": "e1"},
        {"wallet_address": "DUP1", "rule_count": 5, "evidence_level": "y", "triggered_rules": "b", "explanation": "e2"},
        {"wallet_address": "OK1", "rule_count": 1, "evidence_level": "z", "triggered_rules": "c", "explanation": "e3"},
    ])
    by_id, dups, issues = rf.prepare_source(df, rf.FORENSIC_COLUMNS, "synthetic")
    check("duplicate wallet excluded from by_id", "DUP1" not in by_id)
    check("duplicate wallet flagged", "DUP1" in dups)
    check("unique wallet still present", "OK1" in by_id)
    check("issue message recorded", len(issues) == 1)

    r = rf.fuse_wallet("DUP1", None, None, forensic_ambiguous=True)
    check("fused record for duplicate wallet is invalid", r["valid"] is False)
    check("errors mention duplicate forensic rows", "duplicate forensic rows" in r["errors"], r["errors"])


def test_10_missing_wallet_id():
    print("\nTest 10 (extra): rows with a missing/blank wallet_address are dropped, not guessed")
    df = pd.DataFrame([
        {"wallet_address": None, "rule_count": 2, "evidence_level": "x", "triggered_rules": "a", "explanation": "e1"},
        {"wallet_address": "  ", "rule_count": 4, "evidence_level": "x", "triggered_rules": "a", "explanation": "e1"},
        {"wallet_address": "OK2", "rule_count": 1, "evidence_level": "z", "triggered_rules": "c", "explanation": "e3"},
    ])
    by_id, dups, issues = rf.prepare_source(df, rf.FORENSIC_COLUMNS, "synthetic")
    check("only the valid-id row remains", list(by_id.keys()) == ["OK2"], str(by_id.keys()))
    check("issue message recorded for dropped rows", any("missing" in i for i in issues), issues)


def test_end_to_end_real_pipeline():
    print("\nEnd-to-end sanity check: run_fusion() against the real pipeline output files")
    forensic_raw, ml_raw = rf.load_inputs()
    results, source_issues = rf.run_fusion(forensic_raw, ml_raw)
    check("output row count matches input wallet count", len(results) == len(forensic_raw) == len(ml_raw),
          f"{len(results)} vs {len(forensic_raw)}/{len(ml_raw)}")
    check("all real-pipeline rows are valid (files are aligned)", bool(results["valid"].all()))
    check("all forensic_score in [0,1]", results["forensic_score"].between(0, 1).all())
    check("all ml_anomaly_score in [0,1]", results["ml_anomaly_score"].between(0, 1).all())
    check("all combined_score in [0,1]", results["combined_score"].between(0, 1).all())
    check("wallet_address preserved 1:1", set(results["wallet_address"]) == set(forensic_raw["wallet_address"]))
    check("original forensic findings still accessible", (results["forensic_findings"].str.len() > 0).all())
    check("no source-level issues on real (aligned) files", source_issues == [], source_issues)

    sample = results.iloc[0].to_dict()
    nested = rf.to_nested(sample)
    check("to_nested() produces forensic/ml/fusion/validation groups",
          set(nested.keys()) >= {"forensic", "ml", "fusion", "validation"})


def main():
    test_1_normal_valid()
    test_2_strong_forensic_weak_ml()
    test_3_weak_forensic_strong_ml()
    test_4_both_strong()
    test_5_missing_ml_score()
    test_6_invalid_ml_score()
    test_7_missing_forensic_score()
    test_8_missing_evidence()
    test_9_duplicate_wallets()
    test_10_missing_wallet_id()
    test_end_to_end_real_pipeline()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} check(s) FAILED: {FAILURES}")
    else:
        print("RESULT: all checks PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
