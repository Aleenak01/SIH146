"""
Reads the existing synthetic raw dataset (dataset/synthetic_bitcoin_transactions.csv). Read-only.

The file stores every transfer twice (an Outgoing row for the sender and a mirrored Incoming row for
the receiver). This source collapses each pair into one transaction. The file has no transaction ID,
so deterministic surrogate IDs `syn-000001`, `syn-000002`, ... are assigned in chronological order
(stable for identical timestamps); the CSV line number is kept as `source_ref` ('row:<line>').
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..schemas import TransactionIn
from .base import DatasetError, SourcedTransaction, TransactionSource

RAW_COLUMNS = ["timestamp", "wallet_address", "amount_btc", "direction", "input_count", "output_count", "counterparty_wallet"]


class SyntheticCSVSource(TransactionSource):
    source = "synthetic"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> list[SourcedTransaction]:
        import pandas as pd      # imported here: pandas is slow to load and only needed for a CSV import

        if not self.path.is_file():
            raise DatasetError(f"Dataset file not found: {self.path}")

        df = pd.read_csv(self.path, dtype={"timestamp": str, "wallet_address": str, "counterparty_wallet": str, "direction": str})
        if list(df.columns) != RAW_COLUMNS:
            raise DatasetError(f"Unexpected columns {list(df.columns)}; expected {RAW_COLUMNS}")
        if int(df.isna().sum().sum()):
            raise DatasetError("The dataset contains missing values; fix the source data first.")
        if not set(df["direction"]) <= {"Incoming", "Outgoing"}:
            raise DatasetError("Unexpected direction value (expected Incoming/Outgoing).")

        df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S", errors="raise")
        df["line"] = df.index + 2                              # 1-based file line, counting the header

        out = df[df["direction"] == "Outgoing"]
        inc = df[df["direction"] == "Incoming"]

        # Same consistency rule the feature-engineering step relies on: every Outgoing row has its
        # mirrored Incoming row, otherwise a wallet's side of a transfer would be missing.
        sent = Counter(zip(out["timestamp"], out["wallet_address"], out["counterparty_wallet"], out["amount_btc"], out["input_count"], out["output_count"]))
        received = Counter(zip(inc["timestamp"], inc["counterparty_wallet"], inc["wallet_address"], inc["amount_btc"], inc["input_count"], inc["output_count"]))
        if sent != received:
            raise DatasetError("Outgoing and Incoming rows are not mirrored; the dataset is inconsistent.")

        out = out.sort_values("timestamp", kind="stable")     # keeps file order for identical timestamps
        result: list[SourcedTransaction] = []
        for n, row in enumerate(out.itertuples(index=False), start=1):
            try:
                tx = TransactionIn(
                    transaction_id=f"syn-{n:06d}",
                    timestamp=row.timestamp.to_pydatetime(),
                    sender_wallet=row.wallet_address,
                    receiver_wallet=row.counterparty_wallet,
                    amount_btc=float(row.amount_btc),
                    input_count=int(row.input_count),
                    output_count=int(row.output_count),
                )
            except ValueError as e:
                raise DatasetError(f"Invalid transaction at file line {row.line}: {e}") from e
            result.append(SourcedTransaction(tx, f"row:{row.line}"))
        return result
