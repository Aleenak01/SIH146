"""
forensic_rules.py
-----------------
Forensic / behavioural rule layer. It answers: "WHY might this wallet deserve investigator attention?"

Input  : data/wallet_behavior_features.csv   (from ml/feature_engineering.py; not modified)
         data/anomaly_results.csv            (from ml/anomaly_detection.py; not modified)
Output : data/forensic_results.csv

Run from the project root:
    .\\.venv\\Scripts\\python.exe ml\\forensic_rules.py

Pipeline position
    Wallet Behaviour Features -> Isolation Forest -> Anomaly Signal
        -> Forensic Behavioural Rules -> Explainable Behavioural Evidence

Important
  * anomaly_score and anomaly_prediction are copied from the Isolation Forest results, never recalculated.
  * A triggered rule is a BEHAVIOURAL INDICATOR of unusual behaviour. It is supporting evidence for an
    investigation lead that requires investigator review. It is NOT proof of criminal or fraudulent activity.
  * This is not a risk score. evidence_level only describes HOW MANY indicators were triggered.

Feature definitions used (source of truth: ml/feature_engineering.py; nothing is redefined here)
  transaction_count       transactions involving the wallet
  transaction_frequency   transactions per DAY over the wallet's observed period (period floored at 1 hour)
  activity_burst          most transactions the wallet has inside any single 1-hour window
  dormancy_duration       longest gap between consecutive transactions, in HOURS
  fan_in / fan_out        distinct wallets that sent to / received from this wallet
  unique_counterparties   distinct wallets interacted with (direction ignored)
  repeated_connections    counterparties the wallet transacted with more than once
  total_received_btc / total_sent_btc   summed BTC amounts
  incoming_outgoing_ratio total received / total sent, capped at 100 (100 also means "never sent")

Thresholds
  Relative to THIS synthetic wallet population, not real-world claims: a wallet triggers a "high" rule
  when its value is strictly ABOVE the population's 90th percentile for that feature. For the
  imbalance rule, incoming_outgoing_ratio above the 90th percentile (much more received than sent) or
  below the 10th percentile (much more sent than received) triggers it.
"""

from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Settings (paths are relative to the project root)
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATURES_FILE = PROJECT_ROOT / "data" / "wallet_behavior_features.csv"
ANOMALY_FILE = PROJECT_ROOT / "data" / "anomaly_results.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "forensic_results.csv"

HIGH_PERCENTILE = 0.90          # "unusually high" = above this population percentile
LOW_PERCENTILE = 0.10           # used only for the "much more sent than received" side of imbalance

# Simple "high value" rules: (rule name, feature, human-readable explanation)
HIGH_RULES = [
    ("high_transaction_count", "transaction_count",
     "Relatively high transaction activity compared with other wallets."),
    ("high_transaction_frequency", "transaction_frequency",
     "Relatively high transaction frequency (transactions per day)."),
    ("activity_burst", "activity_burst",
     "Many transactions concentrated within a short (1-hour) period."),
    ("long_dormancy", "dormancy_duration",
     "Long inactivity period between consecutive transactions (dormant-period indicator, in hours)."),
    ("fan_out", "fan_out",
     "Funds are distributed across a relatively large number of outgoing connections."),
    ("fan_in", "fan_in",
     "Funds are received through a relatively large number of incoming connections."),
    ("many_counterparties", "unique_counterparties",
     "Interacts with a relatively large number of distinct counterparty wallets."),
    ("repeated_relationships", "repeated_connections",
     "Repeatedly transacts with the same counterparties more often than most wallets."),
    ("high_received_volume", "total_received_btc",
     "Relatively high total BTC received."),
    ("high_sent_volume", "total_sent_btc",
     "Relatively high total BTC sent."),
]
IMBALANCE_RULE = "incoming_outgoing_imbalance"

# Evidence level: describes how many behavioural indicators were triggered (NOT a risk score)
def evidence_level(rule_count):
    if rule_count == 0:
        return "No specific rule triggered"
    if rule_count == 1:
        return "Single behavioural indicator"
    if rule_count <= 3:
        return "Multiple behavioural indicators"
    return "Multiple strong behavioural indicators"


# ----------------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------------
def load_and_merge():
    """Load both inputs and merge on wallet_address (one row per wallet, none dropped)."""
    features = pd.read_csv(FEATURES_FILE)
    anomaly = pd.read_csv(ANOMALY_FILE)

    assert features["wallet_address"].is_unique and anomaly["wallet_address"].is_unique
    assert set(features["wallet_address"]) == set(anomaly["wallet_address"]), \
        "Feature and anomaly files contain different wallets"

    merged = features.merge(anomaly, on="wallet_address", how="inner", validate="one_to_one")
    assert len(merged) == len(features) == len(anomaly)
    return features, anomaly, merged


def compute_thresholds(df):
    """Population-based thresholds (computed from the current synthetic wallets)."""
    thresholds = {feature: float(df[feature].quantile(HIGH_PERCENTILE)) for _, feature, _ in HIGH_RULES}
    ratio = df["incoming_outgoing_ratio"]
    thresholds["ratio_high"] = float(ratio.quantile(HIGH_PERCENTILE))
    thresholds["ratio_low"] = float(ratio.quantile(LOW_PERCENTILE))
    return thresholds


def fmt(x):
    """Compact number formatting for explanations."""
    return f"{x:.0f}" if float(x).is_integer() else f"{x:.3g}"


def evaluate_wallet(row, thresholds):
    """Return a list of (rule_name, explanation) for the rules this wallet triggers."""
    triggered = []

    for name, feature, text in HIGH_RULES:
        if row[feature] > thresholds[feature]:
            pct = int(HIGH_PERCENTILE * 100)
            triggered.append((name, f"{text[:-1]} ({feature}={fmt(row[feature])}; "
                                    f"above the population {pct}th percentile of {fmt(thresholds[feature])})."))

    ratio = row["incoming_outgoing_ratio"]     # existing feature; a value of 100 is the cap
    if ratio > thresholds["ratio_high"]:
        triggered.append((IMBALANCE_RULE,
                          f"Strong incoming/outgoing imbalance: much more BTC received than sent "
                          f"(incoming_outgoing_ratio={fmt(ratio)}, capped at 100; population "
                          f"90th percentile={fmt(thresholds['ratio_high'])})."))
    elif ratio < thresholds["ratio_low"]:
        triggered.append((IMBALANCE_RULE,
                          f"Strong incoming/outgoing imbalance: much more BTC sent than received "
                          f"(incoming_outgoing_ratio={fmt(ratio)}; population "
                          f"10th percentile={fmt(thresholds['ratio_low'])})."))
    return triggered


def run_rules(merged, thresholds):
    """Apply all rules to every wallet and build the output table."""
    rule_names, explanations, counts = [], [], []
    for _, row in merged.iterrows():
        triggered = evaluate_wallet(row, thresholds)
        counts.append(len(triggered))
        if triggered:
            rule_names.append(";".join(name for name, _ in triggered))
            explanations.append(" ".join(text for _, text in triggered))
        else:
            rule_names.append("none")
            explanations.append("No behavioural rule was triggered for this wallet.")

    return pd.DataFrame({
        "wallet_address": merged["wallet_address"],
        "anomaly_score": merged["anomaly_score"],           # copied, not recalculated
        "anomaly_prediction": merged["anomaly_prediction"],  # copied, not recalculated
        "rule_count": counts,
        "evidence_level": [evidence_level(c) for c in counts],
        "triggered_rules": rule_names,
        "explanation": explanations,
    })


def validate_and_report(features, anomaly, results, thresholds):
    # 1-6: structure checks
    assert len(results) == len(features) == len(anomaly) == 410
    assert set(results["wallet_address"]) == set(features["wallet_address"])
    assert results["wallet_address"].is_unique
    assert results.isna().sum().sum() == 0, "Missing values in output"
    # 7-8: Isolation Forest outputs preserved exactly
    original = anomaly.set_index("wallet_address")
    check = results.set_index("wallet_address")
    assert (check["anomaly_score"] == original.loc[check.index, "anomaly_score"]).all(), "anomaly_score changed"
    assert (check["anomaly_prediction"] == original.loc[check.index, "anomaly_prediction"]).all(), \
        "anomaly_prediction changed"
    assert (results["rule_count"] == results["triggered_rules"].map(lambda s: 0 if s == "none" else s.count(";") + 1)).all()

    n = len(results)
    print("\n" + "=" * 60)
    print("FORENSIC RULE ENGINE SUMMARY")
    print("=" * 60)
    print(f"Feature rows                  : {len(features)}")
    print(f"Anomaly-result rows           : {len(anomaly)}")
    print(f"Forensic-result rows          : {n}")
    print(f"All 410 wallets represented   : {set(results['wallet_address']) == set(features['wallet_address'])}")
    print(f"Duplicate wallet addresses    : {int(results['wallet_address'].duplicated().sum())}")
    print(f"Missing values                : {int(results.isna().sum().sum())}")
    print("anomaly_score preserved       : True")
    print("anomaly_prediction preserved  : True")
    print(f"Wallets with >=1 rule         : {int((results['rule_count'] >= 1).sum())}")
    print(f"Wallets with 2+ rules         : {int((results['rule_count'] >= 2).sum())}")

    print("\nThresholds (population-based; a wallet triggers when strictly above, except ratio low side):")
    for k, v in thresholds.items():
        print(f"   {k:<24}{fmt(v)}")

    print("\nRule trigger counts (all wallets / among Isolation Forest 'Anomalous' wallets):")
    anomalous = results["anomaly_prediction"] == "Anomalous"
    every_rule = [r[0] for r in HIGH_RULES] + [IMBALANCE_RULE]
    for name in every_rule:
        has = results["triggered_rules"].str.split(";").map(lambda L: name in L)
        print(f"   {name:<30}{int(has.sum()):>4} / {int((has & anomalous).sum()):>3}")

    print("\nEvidence level by anomaly prediction:")
    print(pd.crosstab(results["evidence_level"], results["anomaly_prediction"]).to_string())

    print("\nTop 10 anomalous wallets (investigation leads requiring investigator review):")
    top = results.sort_values("anomaly_score", ascending=False).head(10)
    print(top[["wallet_address", "anomaly_score", "rule_count", "evidence_level", "triggered_rules"]]
          .to_string(index=False))

    print("\nExample human-readable explanations:")
    for _, r in top.head(3).iterrows():
        print(f"\n{r['wallet_address']} ({r['evidence_level']}):\n   {r['explanation']}")
    print("\nNote: triggered rules are behavioural indicators of unusual behaviour, not proof of "
          "criminal or fraudulent activity. Every lead requires investigator review.")


def main():
    features, anomaly, merged = load_and_merge()
    thresholds = compute_thresholds(merged)
    results = run_rules(merged, thresholds)
    validate_and_report(features, anomaly, results, thresholds)
    results.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved {len(results)} rows to {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
