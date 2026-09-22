"""
result_fusion.py
-----------------
Step 5: Enhanced Hybrid Detection / Result Fusion (WALLET level).

Combines the existing forensic rule evidence (ml/forensic_rules.py -> data/forensic_results.csv)
and the existing ML anomaly evidence (ml/anomaly_detection.py -> data/anomaly_results.csv) into a
single, explainable per-wallet fusion record. Neither upstream component is modified, rerun, or
recalculated here; this module only reads their outputs, validates them, normalizes scores onto a
common 0-1 scale, and combines them with a configurable weighted formula.

Input  : data/forensic_results.csv   (from ml/forensic_rules.py; not modified)
         data/anomaly_results.csv    (from ml/anomaly_detection.py; not modified)
Output : data/fusion_results.csv

Run from the project root:
    .\\.venv\\Scripts\\python.exe ml\\result_fusion.py

Entity level
    This fuses at the WALLET level (wallet_address), matching every upstream component. There is
    no transaction_id anywhere in this pipeline's schema: ml/feature_engineering.py already
    collapses each wallet's full transaction history into one row before Isolation Forest or the
    rule engine ever see it. This is wallet-level hybrid detection, not wallet-level *risk
    classification* (that is a later step) and not transaction-row fusion.

Pipeline position
    Forensic Rules (data/forensic_results.csv)  --\\
                                                     >--  Validation -> Normalization -> Weighted Fusion -> data/fusion_results.csv
    ML Anomaly Detection (data/anomaly_results.csv) --/

Score scale used by fusion
    0 = no suspicious signal, 1 = strongest suspicious signal.
      forensic_score : rule_count / TOTAL_FORENSIC_RULES. forensic_rules.py has no numeric score of
                        its own (only rule_count and the categorical evidence_level), so this is a
                        derived normalization, computed here rather than inside forensic_rules.py.
      ml_score        : anomaly_score from anomaly_detection.py, used AS-IS. It is documented there
                        as "roughly between 0 and 1, higher = more unusual" but never clipped or
                        enforced. This module validates that it is actually within [0, 1] for each
                        wallet instead of assuming it, and never rescales a value that is already
                        in range.

combined_score = FORENSIC_WEIGHT * forensic_score + ML_WEIGHT * ml_score
    FORENSIC_WEIGHT / ML_WEIGHT below are an initial PROTOTYPE configuration used to demonstrate the
    fusion mechanism. They are NOT scientifically validated or tuned against any ground truth and
    should be revisited once labelled / investigator-reviewed outcomes are available.

Boundary validation
    A wallet's forensic or ML evidence is treated as unavailable (not silently defaulted to 0 or
    dropped) when: the source file is missing that wallet, the wallet appears more than once in a
    source (ambiguous), a score is non-numeric or missing, or a score falls outside [0, 1]. When
    either side is unavailable, combined_score is left as None/NaN and the record is marked invalid
    with the reason recorded in `errors`. Evidence that is present but incomplete (e.g. a forensic
    score with no triggered_rules text) is preserved and flagged in `warnings` instead.
"""

from pathlib import Path

import numpy as np
import pandas as pd

import forensic_rules

# ----------------------------------------------------------------------------
# Settings (paths are relative to the project root)
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FORENSIC_FILE = PROJECT_ROOT / "data" / "forensic_results.csv"
ML_FILE = PROJECT_ROOT / "data" / "anomaly_results.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "fusion_results.csv"

ID_COLUMN = "wallet_address"
FORENSIC_COLUMNS = [ID_COLUMN, "rule_count", "evidence_level", "triggered_rules", "explanation"]
ML_COLUMNS = [ID_COLUMN, "anomaly_score", "anomaly_prediction"]

# Total number of behavioural rules forensic_rules.py can trigger, reused (not redefined) so this
# stays in sync if a rule is ever added or removed there.
TOTAL_FORENSIC_RULES = len(forensic_rules.HIGH_RULES) + 1   # +1 for the imbalance rule

# ----------------------------------------------------------------------------
# Fusion configuration -- the ONE place weights and score bounds are defined.
# Prototype weights, NOT scientifically validated: demonstrate the fusion mechanism only.
# ----------------------------------------------------------------------------
FORENSIC_WEIGHT = 0.40
ML_WEIGHT = 0.60
assert abs((FORENSIC_WEIGHT + ML_WEIGHT) - 1.0) < 1e-9, "fusion weights must sum to 1.0"

SCORE_MIN = 0.0
SCORE_MAX = 1.0


# ----------------------------------------------------------------------------
# Step 1: load
# ----------------------------------------------------------------------------
def load_inputs():
    forensic = pd.read_csv(FORENSIC_FILE)
    ml = pd.read_csv(ML_FILE)

    missing_f = [c for c in FORENSIC_COLUMNS if c not in forensic.columns]
    missing_m = [c for c in ML_COLUMNS if c not in ml.columns]
    assert not missing_f, f"{FORENSIC_FILE.name} is missing expected columns: {missing_f}"
    assert not missing_m, f"{ML_FILE.name} is missing expected columns: {missing_m}"
    return forensic, ml


# ----------------------------------------------------------------------------
# Step 2: boundary validation of each source (structural, per-source)
# ----------------------------------------------------------------------------
def prepare_source(df, columns, source_name):
    """
    Return (by_id, dup_ids, issues):
      by_id    : {wallet_address: row_dict} for rows with a usable, unique, non-null wallet_address
      dup_ids  : set of wallet_address values that appear more than once in this source
                 (their evidence is treated as ambiguous/unavailable, never arbitrarily picked)
      issues   : human-readable strings describing what was excluded and why
    """
    issues = []
    df = df[columns].copy()

    missing_id = df[ID_COLUMN].isna() | (df[ID_COLUMN].astype(str).str.strip() == "")
    n_missing_id = int(missing_id.sum())
    if n_missing_id:
        issues.append(f"{source_name}: {n_missing_id} row(s) dropped for missing {ID_COLUMN}")
    df = df[~missing_id]

    dup_ids = set(df.loc[df[ID_COLUMN].duplicated(keep=False), ID_COLUMN])
    if dup_ids:
        sample = ", ".join(sorted(dup_ids)[:5]) + ("..." if len(dup_ids) > 5 else "")
        issues.append(f"{source_name}: {len(dup_ids)} wallet(s) appear more than once "
                       f"(ambiguous, treated as unavailable): {sample}")
    unique_df = df[~df[ID_COLUMN].isin(dup_ids)]

    by_id = unique_df.set_index(ID_COLUMN).to_dict(orient="index")
    return by_id, dup_ids, issues


def all_wallet_ids(forensic_raw, ml_raw):
    ids = set(forensic_raw[ID_COLUMN].dropna()) | set(ml_raw[ID_COLUMN].dropna())
    return sorted(ids)


# ----------------------------------------------------------------------------
# Step 3: score normalization (explicit, in one place, easy to change)
# ----------------------------------------------------------------------------
def _is_missing(value):
    return value is None or (isinstance(value, float) and np.isnan(value))


def normalize_forensic_score(rule_count):
    """rule_count -> forensic_score in [0, 1]. Returns (score_or_None, error_or_None)."""
    if _is_missing(rule_count):
        return None, "forensic rule_count is missing"
    try:
        rule_count = float(rule_count)
    except (TypeError, ValueError):
        return None, f"forensic rule_count is not numeric: {rule_count!r}"
    if rule_count < 0 or rule_count > TOTAL_FORENSIC_RULES:
        return None, f"forensic rule_count out of expected range [0, {TOTAL_FORENSIC_RULES}]: {rule_count}"

    score = rule_count / TOTAL_FORENSIC_RULES
    if not (SCORE_MIN <= score <= SCORE_MAX):
        return None, f"derived forensic_score out of [0, 1]: {score}"
    return round(score, 6), None


def normalize_ml_score(anomaly_score):
    """
    ML anomaly_score -> ml_score in [0, 1]. anomaly_detection.py already documents this score as
    roughly 0-1 (higher = more unusual), so it is validated here, not rescaled -- an already
    normalized score is not distorted. Returns (score_or_None, error_or_None).
    """
    if _is_missing(anomaly_score):
        return None, "ML anomaly_score is missing"
    try:
        anomaly_score = float(anomaly_score)
    except (TypeError, ValueError):
        return None, f"ML anomaly_score is not numeric: {anomaly_score!r}"
    if not (SCORE_MIN <= anomaly_score <= SCORE_MAX):
        return None, f"ML anomaly_score out of expected [0, 1] range: {anomaly_score}"
    return round(anomaly_score, 6), None


# ----------------------------------------------------------------------------
# Step 4: per-wallet validation + fusion
# ----------------------------------------------------------------------------
def fuse_wallet(wallet_id, forensic_row, ml_row, forensic_ambiguous=False, ml_ambiguous=False):
    """
    Validate and fuse one wallet's forensic + ML evidence.

    forensic_row / ml_row : dict of that wallet's row from the respective source, or None if the
                             wallet is absent from that source.
    forensic_ambiguous / ml_ambiguous : True if the wallet_address was duplicated in that source
                             (evidence is then treated as unavailable, never arbitrarily chosen).

    Never invents forensic or ML findings: fields whose source is unavailable are left None/empty,
    not defaulted to a "safe-looking" value.
    """
    warnings, errors = [], []

    # --- forensic side ---------------------------------------------------------------------
    rule_count = evidence_level = triggered_rules = findings = None
    if forensic_ambiguous:
        errors.append("duplicate forensic rows for this wallet_address; forensic evidence unavailable")
    elif forensic_row is None:
        errors.append("forensic result missing for this wallet")
    else:
        rule_count = forensic_row.get("rule_count")
        evidence_level = forensic_row.get("evidence_level")
        triggered_rules = forensic_row.get("triggered_rules")
        findings = forensic_row.get("explanation")

    forensic_score = None
    if forensic_row is not None and not forensic_ambiguous:
        forensic_score, forensic_error = normalize_forensic_score(rule_count)
        if forensic_error:
            errors.append(f"forensic: {forensic_error}")
        else:
            rules_missing = _is_missing(triggered_rules) or str(triggered_rules).strip() == ""
            if rule_count and float(rule_count) > 0 and rules_missing:
                warnings.append("forensic score present but triggered_rules evidence is missing")

    # --- ML side -----------------------------------------------------------------------------
    anomaly_prediction = None
    if ml_ambiguous:
        errors.append("duplicate ML rows for this wallet_address; ML evidence unavailable")
    elif ml_row is None:
        errors.append("ML result missing for this wallet")
    else:
        anomaly_prediction = ml_row.get("anomaly_prediction")

    ml_score = None
    if ml_row is not None and not ml_ambiguous:
        ml_score, ml_error = normalize_ml_score(ml_row.get("anomaly_score"))
        if ml_error:
            errors.append(f"ML: {ml_error}")

    # --- fusion ------------------------------------------------------------------------------
    valid = forensic_score is not None and ml_score is not None
    combined_score = None
    if valid:
        combined_score = round(FORENSIC_WEIGHT * forensic_score + ML_WEIGHT * ml_score, 6)
        if not (SCORE_MIN <= combined_score <= SCORE_MAX):
            # Not reachable in practice (a convex combination of two in-range scores stays in
            # range), kept as an explicit safety net rather than trusting the arithmetic blindly.
            errors.append(f"combined_score out of [0, 1]: {combined_score}")
            combined_score = None
            valid = False

    return {
        "wallet_address": wallet_id,
        "forensic_score": forensic_score,
        "forensic_rule_count": rule_count,
        "forensic_evidence_level": evidence_level,
        "forensic_triggered_rules": triggered_rules,
        "forensic_findings": findings,
        "ml_anomaly_score": ml_score,
        "ml_anomaly_prediction": anomaly_prediction,
        "combined_score": combined_score,
        "forensic_weight": FORENSIC_WEIGHT,
        "ml_weight": ML_WEIGHT,
        "valid": valid,
        "warnings": ";".join(warnings),
        "errors": ";".join(errors),
    }


def run_fusion(forensic_raw, ml_raw):
    forensic_by_id, forensic_dups, forensic_issues = prepare_source(forensic_raw, FORENSIC_COLUMNS, "forensic_results.csv")
    ml_by_id, ml_dups, ml_issues = prepare_source(ml_raw, ML_COLUMNS, "anomaly_results.csv")

    records = [
        fuse_wallet(
            wid,
            forensic_by_id.get(wid),
            ml_by_id.get(wid),
            forensic_ambiguous=wid in forensic_dups,
            ml_ambiguous=wid in ml_dups,
        )
        for wid in all_wallet_ids(forensic_raw, ml_raw)
    ]
    results = pd.DataFrame(records)
    return results, forensic_issues + ml_issues


# ----------------------------------------------------------------------------
# Step 5: nested, investigator-facing view (explainability helper; storage stays flat, matching
# the project's existing CSV convention -- this just reshapes one row on request)
# ----------------------------------------------------------------------------
def to_nested(record):
    """Reshape one flat fusion row/dict into the conceptual forensic/ml/fusion/validation view."""
    warnings = record["warnings"].split(";") if record["warnings"] else []
    errors = record["errors"].split(";") if record["errors"] else []
    return {
        "wallet_address": record["wallet_address"],
        "forensic": {
            "score": record["forensic_score"],
            "rule_count": record["forensic_rule_count"],
            "evidence_level": record["forensic_evidence_level"],
            "triggered_rules": record["forensic_triggered_rules"],
            "findings": record["forensic_findings"],
        },
        "ml": {
            "anomaly_score": record["ml_anomaly_score"],
            "anomaly_prediction": record["ml_anomaly_prediction"],
        },
        "fusion": {
            "combined_score": record["combined_score"],
            "forensic_weight": record["forensic_weight"],
            "ml_weight": record["ml_weight"],
        },
        "validation": {
            "valid": bool(record["valid"]),
            "warnings": warnings,
            "errors": errors,
        },
    }


# ----------------------------------------------------------------------------
# Step 6: validation + report
# ----------------------------------------------------------------------------
def validate_and_report(forensic_raw, ml_raw, results, source_issues):
    assert results[ID_COLUMN].is_unique, "fusion output has duplicate wallet_address rows"
    for col in ("forensic_score", "ml_anomaly_score", "combined_score"):
        vals = results.loc[results[col].notna(), col]
        assert ((vals >= SCORE_MIN) & (vals <= SCORE_MAX)).all(), f"{col} has values outside [0, 1]"
    assert (results.loc[results["valid"], "combined_score"].notna()).all()
    assert (results.loc[~results["valid"], "combined_score"].isna()).all()

    n = len(results)
    n_valid = int(results["valid"].sum())

    print("\n" + "=" * 60)
    print("RESULT FUSION SUMMARY (Step 5 -- wallet-level hybrid detection)")
    print("=" * 60)
    print(f"Forensic-result rows          : {len(forensic_raw)}")
    print(f"ML-result rows                : {len(ml_raw)}")
    print(f"Fusion-result rows            : {n}")
    print(f"Fusion weights                : forensic={FORENSIC_WEIGHT}, ml={ML_WEIGHT} "
          f"(prototype, not scientifically validated)")
    print(f"Valid (fully fused)           : {n_valid}")
    print(f"Invalid / degraded            : {n - n_valid}")

    if source_issues:
        print("\nSource-level issues detected:")
        for issue in source_issues:
            print(f"   - {issue}")
    else:
        print("\nSource-level issues detected  : none")

    n_warn = int((results["warnings"] != "").sum())
    print(f"Rows with warnings (evidence present but incomplete): {n_warn}")

    valid_scores = results.loc[results["valid"], "combined_score"]
    if len(valid_scores):
        print(f"combined_score min / max / mean: {valid_scores.min():.4f} / "
              f"{valid_scores.max():.4f} / {valid_scores.mean():.4f}")

    print("\nTop 10 wallets by combined_score (valid rows only):")
    top = results[results["valid"]].sort_values("combined_score", ascending=False).head(10)
    print(top[["wallet_address", "forensic_score", "ml_anomaly_score", "combined_score",
               "forensic_evidence_level"]].to_string(index=False))

    if n - n_valid:
        print("\nInvalid / degraded rows (evidence unavailable or out of range):")
        bad = results[~results["valid"]]
        print(bad[["wallet_address", "forensic_score", "ml_anomaly_score", "errors"]]
              .head(10).to_string(index=False))

    print("\nNote: combined_score is a prototype fusion of existing forensic and ML evidence, not a "
          "final risk classification. Every flagged wallet remains an investigation lead requiring "
          "investigator review.")


def main():
    forensic_raw, ml_raw = load_inputs()
    results, source_issues = run_fusion(forensic_raw, ml_raw)
    validate_and_report(forensic_raw, ml_raw, results, source_issues)

    results.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved {len(results)} rows to {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
