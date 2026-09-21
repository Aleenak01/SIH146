"""
feature_engineering.py
----------------------
Turns the TRANSACTION-level raw dataset into a WALLET-level behavioural feature dataset
(one row per wallet). This is the input that Isolation Forest will use in a later step.

Input  : data/synthetic_bitcoin_transactions.csv
         (if that file is not there, dataset/synthetic_bitcoin_transactions.csv is used)
Output : data/wallet_behavior_features.csv

Run from the project root:
    .\\.venv\\Scripts\\python.exe ml\\feature_engineering.py

How the raw data is read
  Every transfer A -> B appears as two mirrored rows in the raw CSV:
      A | Outgoing | counterparty B      and      B | Incoming | counterparty A
  So the rows where wallet_address == W are exactly the transactions that involve W (as sender or
  receiver). We check that this mirroring holds before relying on it, so no transaction is lost.

Notes
  * Features describe each wallet's whole observed history (an offline snapshot of the dataset).
  * Time gaps are in HOURS, frequency is transactions per DAY.
  * Wallets with fewer than 2 transactions have no time gaps. Those time features are left as NaN
    (missing) instead of invented. In the current dataset every wallet has 2+ transactions.
  * Only the raw CSV columns are used; no anomaly labels or scores exist here.
"""

from collections import Counter, deque
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Settings (paths are relative to the project root, not hard-coded)
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CANDIDATES = [PROJECT_ROOT / "data" / "synthetic_bitcoin_transactions.csv",
                  PROJECT_ROOT / "dataset" / "synthetic_bitcoin_transactions.csv"]
OUTPUT_FILE = PROJECT_ROOT / "data" / "wallet_behavior_features.csv"

RAW_COLUMNS = ["timestamp", "wallet_address", "amount_btc", "direction",
               "input_count", "output_count", "counterparty_wallet"]

FEATURE_COLUMNS = [
    # transaction behaviour
    "transaction_count", "incoming_count", "outgoing_count",
    "total_received_btc", "total_sent_btc", "avg_transaction_amount",
    # time behaviour
    "time_since_previous_tx", "avg_transaction_interval", "transaction_frequency",
    "dormancy_duration", "activity_burst",
    # flow behaviour
    "incoming_outgoing_ratio", "fan_in", "fan_out",
    # network / relationship behaviour
    "unique_counterparties", "wallet_degree", "repeated_connections", "hop_distance",
]

BURST_WINDOW_SECONDS = 3600     # activity_burst looks at 1-hour windows
MIN_PERIOD_HOURS = 1.0          # smallest activity period used for transaction_frequency
MAX_RATIO = 100.0               # cap for incoming_outgoing_ratio (see build function)


# ----------------------------------------------------------------------------
# Step 1: load and check the raw data
# ----------------------------------------------------------------------------
def load_raw():
    """Read the raw CSV, check it, parse timestamps and sort chronologically."""
    path = next((p for p in RAW_CANDIDATES if p.exists()), None)
    if path is None:
        raise FileNotFoundError("Raw CSV not found in: " + ", ".join(str(p) for p in RAW_CANDIDATES))
    print(f"Reading raw data from: {path.relative_to(PROJECT_ROOT)}")

    df = pd.read_csv(path)
    assert list(df.columns) == RAW_COLUMNS, f"Unexpected raw columns: {list(df.columns)}"

    # Missing values are reported, never silently dropped.
    n_missing = int(df.isna().sum().sum())
    assert n_missing == 0, f"Raw data has {n_missing} missing values; fix the source data first."

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S", errors="raise")
    assert set(df["direction"]) <= {"Incoming", "Outgoing"}, "Unexpected direction value"
    assert (df["amount_btc"] > 0).all(), "Non-positive amount found"

    # Every Outgoing row must have its mirrored Incoming row (and vice versa).
    out = df[df["direction"] == "Outgoing"]
    inc = df[df["direction"] == "Incoming"]
    sent = Counter(zip(out["timestamp"], out["wallet_address"], out["counterparty_wallet"], out["amount_btc"]))
    received = Counter(zip(inc["timestamp"], inc["counterparty_wallet"], inc["wallet_address"], inc["amount_btc"]))
    assert sent == received, "Outgoing and Incoming rows are not mirrored; a wallet's side could be missing."

    # Stable sort keeps rows with identical timestamps in their original order.
    return df.sort_values("timestamp", kind="stable").reset_index(drop=True)


# ----------------------------------------------------------------------------
# Step 2: network distance (hop_distance)
# ----------------------------------------------------------------------------
def compute_hop_distance(df):
    """
    hop_distance (simplified version): the AVERAGE NUMBER OF HOPS from a wallet to every other
    wallet it can reach through the wallet <-> counterparty links.

      * The graph has one node per wallet and an edge between two wallets if they ever transacted.
      * Direction and time are ignored (plain "how many links apart are these wallets").
      * Shortest paths are found with a breadth-first search (BFS) from each wallet.
      * Wallets that cannot be reached are ignored. A wallet with no links gets NaN.
      * Low value = well connected / central; high value = far from the rest of the network.

    This is a simplification: it does not follow time-ordered fund paths.
    """
    neighbours = {}
    for a, b in zip(df["wallet_address"], df["counterparty_wallet"]):
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)

    result = {}
    for start in neighbours:
        distance = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for nxt in neighbours[current]:
                if nxt not in distance:
                    distance[nxt] = distance[current] + 1
                    queue.append(nxt)
        others = [d for w, d in distance.items() if w != start]
        result[start] = float(np.mean(others)) if others else np.nan
    return result


# ----------------------------------------------------------------------------
# Step 3: per-wallet features
# ----------------------------------------------------------------------------
def build_wallet_features(df):
    """Return one row per wallet with all engineered features."""
    hop = compute_hop_distance(df)
    rows = []

    for wallet, g in df.groupby("wallet_address", sort=True):
        g = g.sort_values("timestamp", kind="stable")      # chronological for this wallet
        n = len(g)
        incoming = g[g["direction"] == "Incoming"]
        outgoing = g[g["direction"] == "Outgoing"]

        # --- Transaction behaviour ---------------------------------------------------------
        total_received = float(incoming["amount_btc"].sum())
        total_sent = float(outgoing["amount_btc"].sum())

        # --- Time behaviour ----------------------------------------------------------------
        # seconds since the wallet's first transaction, one value per transaction
        secs = (g["timestamp"] - g["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
        gaps = np.diff(secs)                                # seconds between consecutive transactions

        if n >= 2:
            time_since_previous = gaps[-1] / 3600           # latest gap, hours
            avg_interval = gaps.mean() / 3600               # mean gap, hours
            dormancy = gaps.max() / 3600                    # longest gap, hours
            # transactions per day over the observed activity period (period is at least 1 hour
            # so wallets whose transactions are all at one instant do not divide by zero)
            period_days = max(secs[-1] / 3600, MIN_PERIOD_HOURS) / 24
            frequency = n / period_days
        else:
            time_since_previous = avg_interval = dormancy = frequency = np.nan

        # activity_burst = the largest number of this wallet's transactions that fall inside any
        # single 1-hour window (window starts at each of its transactions). A wallet with steady,
        # spread-out activity scores low; one that does many transactions at once scores high.
        window_end = np.searchsorted(secs, secs + BURST_WINDOW_SECONDS, side="left")
        burst = int((window_end - np.arange(n)).max())

        # --- Flow behaviour ----------------------------------------------------------------
        # received / sent. If the wallet never sent anything the ratio is undefined (division by
        # zero), so it is set to the cap MAX_RATIO; other ratios are capped at MAX_RATIO too so
        # one extreme wallet cannot dominate the scale.
        if total_sent > 0:
            ratio = min(total_received / total_sent, MAX_RATIO)
        else:
            ratio = MAX_RATIO if total_received > 0 else np.nan

        fan_in = incoming["counterparty_wallet"].nunique()      # distinct wallets that sent to it
        fan_out = outgoing["counterparty_wallet"].nunique()     # distinct wallets it sent to

        # --- Network / relationship behaviour ---------------------------------------------
        # unique_counterparties: distinct wallets it interacted with, direction ignored.
        # wallet_degree: number of distinct DIRECTED links = fan_in + fan_out. A partner that both
        #   sends to and receives from the wallet counts twice, so degree >= unique_counterparties.
        # repeated_connections: counterparties it transacted with more than once (any direction).
        per_counterparty = g["counterparty_wallet"].value_counts()

        rows.append({
            "wallet_address": wallet,
            "transaction_count": n,
            "incoming_count": len(incoming),
            "outgoing_count": len(outgoing),
            "total_received_btc": total_received,
            "total_sent_btc": total_sent,
            "avg_transaction_amount": float(g["amount_btc"].mean()),
            "time_since_previous_tx": time_since_previous,
            "avg_transaction_interval": avg_interval,
            "transaction_frequency": frequency,
            "dormancy_duration": dormancy,
            "activity_burst": burst,
            "incoming_outgoing_ratio": ratio,
            "fan_in": fan_in,
            "fan_out": fan_out,
            "unique_counterparties": len(per_counterparty),
            "wallet_degree": fan_in + fan_out,
            "repeated_connections": int((per_counterparty > 1).sum()),
            "hop_distance": hop.get(wallet, np.nan),
        })

    return pd.DataFrame(rows, columns=["wallet_address"] + FEATURE_COLUMNS)


# ----------------------------------------------------------------------------
# Step 4: validation
# ----------------------------------------------------------------------------
def validate(raw, features):
    assert "wallet_address" in features.columns
    missing_cols = [c for c in FEATURE_COLUMNS if c not in features.columns]
    assert not missing_cols, f"Missing feature columns: {missing_cols}"
    assert features["wallet_address"].is_unique, "wallet_address must be unique (one row per wallet)"
    for c in FEATURE_COLUMNS:
        assert pd.api.types.is_numeric_dtype(features[c]), f"{c} is not numeric"

    # Every wallet in the raw data (either column) must be present.
    all_wallets = set(raw["wallet_address"]) | set(raw["counterparty_wallet"])
    assert set(features["wallet_address"]) == all_wallets, "Some wallets are missing from the features"

    # Consistency checks between features.
    assert (features["incoming_count"] + features["outgoing_count"] == features["transaction_count"]).all()
    assert features["transaction_count"].sum() == len(raw), "Transaction counts do not add up to raw rows"
    assert (features["wallet_degree"] >= features["unique_counterparties"]).all()

    # NaN is only allowed in time features, and only for wallets with fewer than 2 transactions.
    time_cols = ["time_since_previous_tx", "avg_transaction_interval", "transaction_frequency", "dormancy_duration"]
    other_cols = [c for c in FEATURE_COLUMNS if c not in time_cols]
    assert not features[other_cols].isna().any().any(), "Unexpected NaN in a non-time feature"
    assert not features.loc[features["transaction_count"] >= 2, time_cols].isna().any().any(), \
        "Unexpected NaN in time features of a wallet with 2+ transactions"
    print("All validation checks passed.")


def print_summary(raw, features):
    print("\n" + "=" * 60)
    print("FEATURE ENGINEERING SUMMARY")
    print("=" * 60)
    print(f"Raw transaction rows          : {len(raw)}")
    print(f"Unique wallets (raw data)     : {len(set(raw['wallet_address']) | set(raw['counterparty_wallet']))}")
    print(f"Engineered wallet rows        : {len(features)}")
    print(f"Output columns ({len(features.columns)})            : {list(features.columns)}")

    print("\nPer-feature: missing values, data type, min, max")
    print(f"{'feature':<26}{'missing':>8}  {'dtype':<8}{'min':>16}{'max':>16}")
    for c in features.columns:
        if c == "wallet_address":
            print(f"{c:<26}{int(features[c].isna().sum()):>8}  {str(features[c].dtype):<8}{'-':>16}{'-':>16}")
        else:
            print(f"{c:<26}{int(features[c].isna().sum()):>8}  {str(features[c].dtype):<8}"
                  f"{features[c].min():>16.6g}{features[c].max():>16.6g}")

    print("\nFirst 10 rows:")
    with pd.option_context("display.width", 250, "display.max_columns", None):
        print(features.head(10).to_string(index=False))


def main():
    raw = load_raw()
    features = build_wallet_features(raw)
    validate(raw, features)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    features.round(6).to_csv(OUTPUT_FILE, index=False)
    print(f"Saved {len(features)} wallet rows to {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")
    print_summary(raw, features)


if __name__ == "__main__":
    main()
