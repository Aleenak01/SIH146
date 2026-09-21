"""
anomaly_detection.py
--------------------
Runs Isolation Forest (unsupervised) on the wallet behaviour features and saves the results.

Input  : data/wallet_behavior_features.csv   (from ml/feature_engineering.py; not modified)
Output : data/anomaly_results.csv            (wallet_address, anomaly_score, anomaly_prediction)

Run from the project root:
    .\\.venv\\Scripts\\python.exe ml\\anomaly_detection.py

What this does and does NOT mean
  * There are no labels. The model is never told which wallets are "suspicious"; it only looks for
    wallets whose combination of behavioural features is unusual compared with the other wallets.
  * "Anomalous" means UNUSUAL, not criminal. Every flagged wallet is an investigation lead that
    requires investigator review.

anomaly_score (higher = more unusual)
  anomaly_score = -1 * IsolationForest.score_samples(X)
  This is the standard Isolation Forest score. It is roughly between 0 and 1:
    * close to 1   -> the wallet is isolated after very few random splits -> very unusual
    * around 0.5   -> not clearly different from the rest
    * well below 0.5 -> typical of the wallet population
  It is a relative measure within THIS wallet population, not a probability and not a risk score.

anomaly_prediction
  "Anomalous" if the model's threshold (set by `contamination`) flags the wallet, else "Normal".
  This is exactly the model's own predict() decision, so it is consistent with the score.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

# ----------------------------------------------------------------------------
# Settings (paths are relative to the project root)
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = PROJECT_ROOT / "data" / "wallet_behavior_features.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "anomaly_results.csv"

ID_COLUMN = "wallet_address"
FEATURE_COLUMNS = [
    "transaction_count", "incoming_count", "outgoing_count",
    "total_received_btc", "total_sent_btc", "avg_transaction_amount",
    "time_since_previous_tx", "avg_transaction_interval", "transaction_frequency",
    "dormancy_duration", "activity_burst",
    "incoming_outgoing_ratio", "fan_in", "fan_out",
    "unique_counterparties", "wallet_degree", "repeated_connections", "hop_distance",
]

# Model configuration
RANDOM_STATE = 42       # fixed seed -> reproducible results
N_ESTIMATORS = 200      # number of random trees (more trees = more stable scores)
CONTAMINATION = 0.10    # expected share of unusual wallets; see note below

# Why 0.10?  Isolation Forest needs an assumed share of unusual points to place its threshold.
# There are no labels, so this is a prototype assumption, not a measured fact: about 1 wallet in 10
# is sent for review. It gives a manageable lead list (~41 of 410) and is in line with the
# 10-15% unusual activity we aimed for when generating the synthetic data. Change it to
# change how many wallets are flagged; the scores themselves do not depend on it.


# ----------------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------------
def load_features():
    """Load the feature CSV and check it. Wallets are never dropped."""
    df = pd.read_csv(INPUT_FILE)

    assert ID_COLUMN in df.columns, f"{ID_COLUMN} column missing"
    missing_cols = [c for c in FEATURE_COLUMNS if c not in df.columns]
    assert not missing_cols, f"Missing feature columns: {missing_cols}"
    assert df[ID_COLUMN].is_unique, "wallet_address must be unique"

    X = df[FEATURE_COLUMNS]
    for c in FEATURE_COLUMNS:
        assert pd.api.types.is_numeric_dtype(X[c]), f"{c} is not numeric"

    # Isolation Forest cannot handle NaN. We stop instead of silently dropping wallets or
    # inventing values; the current feature file has none.
    n_nan = int(X.isna().sum().sum())
    assert n_nan == 0, f"{n_nan} missing feature values; decide how to handle them before modelling."
    assert np.isfinite(X.to_numpy()).all(), "Infinite values found in features"
    return df


def run_isolation_forest(features_df):
    """
    Fit Isolation Forest on the numeric features and return the results table.

    No scaling is applied: Isolation Forest splits each feature at random points inside that
    feature's own range, so it is not affected by features having different units or scales
    (e.g. BTC amounts vs hours vs counts). wallet_address is not given to the model.
    """
    X = features_df[FEATURE_COLUMNS].to_numpy(dtype=float)

    model = IsolationForest(n_estimators=N_ESTIMATORS, contamination=CONTAMINATION,
                            random_state=RANDOM_STATE)
    model.fit(X)

    # score_samples: lower = more unusual (negative numbers). Flip the sign so that a HIGHER
    # anomaly_score means MORE unusual, which is easier to read.
    anomaly_score = -model.score_samples(X)
    prediction = np.where(model.predict(X) == -1, "Anomalous", "Normal")   # predict: -1 = anomaly

    return pd.DataFrame({
        ID_COLUMN: features_df[ID_COLUMN],
        "anomaly_score": np.round(anomaly_score, 6),
        "anomaly_prediction": prediction,
    })


def validate_and_report(features_df, results):
    # Output checks
    assert len(results) == len(features_df) == 410, f"Expected 410 wallets, got {len(results)}"
    assert set(results[ID_COLUMN]) == set(features_df[ID_COLUMN]), "Wallets differ from the input"
    assert results.isna().sum().sum() == 0, "Missing values in the output"
    assert set(results["anomaly_prediction"]) <= {"Anomalous", "Normal"}

    n = len(results)
    n_anom = int((results["anomaly_prediction"] == "Anomalous").sum())
    s = results["anomaly_score"]

    print("\n" + "=" * 60)
    print("ISOLATION FOREST SUMMARY")
    print("=" * 60)
    print(f"Wallets analysed              : {n}")
    print(f"Numerical features used ({len(FEATURE_COLUMNS)})   : {FEATURE_COLUMNS}")
    print(f"Model                         : IsolationForest(n_estimators={N_ESTIMATORS}, "
          f"contamination={CONTAMINATION}, random_state={RANDOM_STATE})")
    print(f"Classified as anomalous       : {n_anom}")
    print(f"Classified as normal          : {n - n_anom}")
    print(f"Percentage anomalous          : {n_anom / n:.1%}")
    print(f"Anomaly score  min / max / mean: {s.min():.4f} / {s.max():.4f} / {s.mean():.4f}   (higher = more unusual)")
    print(f"Output has exactly 410 wallets: {len(results) == 410}")
    print(f"Missing values in output      : {int(results.isna().sum().sum())}")

    print("\nFirst 10 results:")
    print(results.head(10).to_string(index=False))

    print("\nTop 10 most unusual wallets (investigation leads requiring investigator review):")
    print(results.sort_values("anomaly_score", ascending=False).head(10).to_string(index=False))
    print("\nNote: 'Anomalous' means statistically unusual behaviour, not confirmed suspicious "
          "or criminal activity.")


def main():
    features_df = load_features()
    results = run_isolation_forest(features_df)
    validate_and_report(features_df, results)
    results.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved {len(results)} rows to {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
