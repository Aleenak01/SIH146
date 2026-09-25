"""
Runs the EXISTING analysis pipeline (ml/) in memory on transactions taken from the database.

Nothing in ml/ is modified or re-implemented here. The four stages are the same functions the CLI scripts
use, in the same order and with the same settings:

    ml/feature_engineering.build_wallet_features   raw mirrored rows -> 18 wallet features
    ml/anomaly_detection.run_isolation_forest      Isolation Forest (unsupervised)
    ml/forensic_rules.compute_thresholds/run_rules 11 behavioural rules
    ml/result_fusion.run_fusion                    forensic + ML evidence -> combined_score

Equivalence with the CLI is checked by a test that compares this module's output for the project dataset
with data/*.csv. Two details are needed for that: the CLI rounds features to 6 decimals before modelling, and
the raw frame must be in the CLI's row layout (an Outgoing row plus a mirrored Incoming row per transfer).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import PROJECT_ROOT

ML_DIR = PROJECT_ROOT / "ml"

# Wallets need at least this many scored wallets for the population-relative model and percentile rules to mean anything.
MIN_SCORED_WALLETS = 20


class AnalysisError(RuntimeError):
    """The analysis cannot run (not enough data, ...). Expected, reported to the user, never fabricated around."""


def load_ml():
    """Import the ml/ modules lazily (pandas and scikit-learn are slow to load, and not needed to serve the API)."""
    if str(ML_DIR) not in sys.path:
        sys.path.insert(0, str(ML_DIR))       # result_fusion imports its sibling `forensic_rules`
    import anomaly_detection as ad
    import feature_engineering as fe
    import forensic_rules as fr
    import result_fusion as rf

    return fe, ad, fr, rf


@dataclass
class PipelineOutput:
    features: Any                                  # DataFrame: features of the SCORED wallets (rounded to 6 dp)
    ml: Any                                        # DataFrame: wallet_address, anomaly_score, anomaly_prediction
    forensic: Any                                  # DataFrame: as data/forensic_results.csv
    fusion: Any                                    # DataFrame: as data/fusion_results.csv
    findings: dict[str, list[dict[str, Any]]]      # wallet -> triggered rules with value/threshold/evidence
    thresholds: dict[str, float]
    unscored: dict[str, str] = field(default_factory=dict)     # wallet -> why it could not be scored
    config: dict[str, Any] = field(default_factory=dict)


def build_raw_frame(transfers: Sequence[tuple]):
    """
    transfers: (timestamp, sender, receiver, amount_btc, input_count, output_count), in chronological order.
    Returns the CLI's raw layout: for every transfer an Outgoing row (sender) and a mirrored Incoming row (receiver).
    """
    import pandas as pd

    rows = []
    for ts, snd, rcv, amt, n_in, n_out in transfers:
        rows.append((ts, snd, amt, "Outgoing", n_in, n_out, rcv))
        rows.append((ts, rcv, amt, "Incoming", n_in, n_out, snd))
    df = pd.DataFrame(rows, columns=["timestamp", "wallet_address", "amount_btc", "direction", "input_count", "output_count", "counterparty_wallet"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp", kind="stable").reset_index(drop=True)


def run_pipeline(transfers: Sequence[tuple]) -> PipelineOutput:
    import numpy as np

    fe, ad, fr, rf = load_ml()
    if not transfers:
        raise AnalysisError("There are no transactions to analyse.")

    raw = build_raw_frame(transfers)
    features_all = fe.build_wallet_features(raw).round(6)           # same rounding as feature_engineering.main()

    # Isolation Forest cannot take missing values, and a value must never be invented to fill one. A wallet with a
    # single transaction has no time gaps, so its time features do not exist: it is left unscored (and reported).
    feature_matrix = features_all[fe.FEATURE_COLUMNS]
    complete = feature_matrix.notna().all(axis=1) & np.isfinite(feature_matrix.to_numpy(dtype=float)).all(axis=1)
    unscored = {
        w: ("fewer than 2 transactions: time-based features cannot be computed" if n < 2 else "some engineered features are unavailable")
        for w, n in zip(features_all.loc[~complete, "wallet_address"], features_all.loc[~complete, "transaction_count"])
    }
    features = features_all[complete].reset_index(drop=True)
    if len(features) < MIN_SCORED_WALLETS:
        raise AnalysisError(f"Only {len(features)} wallets have complete features; at least {MIN_SCORED_WALLETS} are needed for the model and percentile rules to be meaningful.")

    ml = ad.run_isolation_forest(features)
    merged = features.merge(ml, on="wallet_address", how="inner", validate="one_to_one")
    thresholds = fr.compute_thresholds(merged)
    forensic = fr.run_rules(merged, thresholds)
    fusion, issues = rf.run_fusion(forensic, ml)
    if issues:
        raise AnalysisError("Result fusion reported source issues: " + "; ".join(issues))

    # Per-rule evidence (rule, feature value, threshold, the rule's own sentence) using the rule engine's own function.
    rule_feature = {name: feature for name, feature, _ in fr.HIGH_RULES}
    findings: dict[str, list[dict[str, Any]]] = {}
    for _, row in merged.iterrows():
        items = []
        for name, sentence in fr.evaluate_wallet(row, thresholds):
            if name == fr.IMBALANCE_RULE:
                feature, value = "incoming_outgoing_ratio", float(row["incoming_outgoing_ratio"])
                threshold = thresholds["ratio_high"] if value > thresholds["ratio_high"] else thresholds["ratio_low"]
            else:
                feature = rule_feature[name]
                value, threshold = float(row[feature]), float(thresholds[feature])
            items.append({"rule_id": name, "feature": feature, "value": value, "threshold": float(threshold), "evidence": sentence})
        if items:
            findings[row["wallet_address"]] = items

    config = {
        "isolation_forest": {"n_estimators": ad.N_ESTIMATORS, "contamination": ad.CONTAMINATION, "random_state": ad.RANDOM_STATE, "unsupervised": True},
        "forensic_rules": {"rule_count": len(fr.HIGH_RULES) + 1, "high_percentile": fr.HIGH_PERCENTILE, "low_percentile": fr.LOW_PERCENTILE},
        "fusion": {"forensic_weight": rf.FORENSIC_WEIGHT, "ml_weight": rf.ML_WEIGHT, "total_forensic_rules": rf.TOTAL_FORENSIC_RULES,
                   "note": "Prototype fusion weighting - not statistically validated."},
        "thresholds": {k: float(v) for k, v in thresholds.items()},
        "scored_wallets": int(len(features)),
        "unscored_wallets": int(len(unscored)),
    }
    return PipelineOutput(features, ml, forensic, fusion, findings, {k: float(v) for k, v in thresholds.items()}, unscored, config)
