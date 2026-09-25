"""
The transaction-source abstraction.

Every source (synthetic CSV, synthetic stream, real Bitcoin) produces the same normalized object,
`SourcedTransaction`, so the database and the analysis pipeline never care where a transaction
came from. The `source` label is recorded with every row and is never guessed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..schemas import TransactionIn


class DatasetError(ValueError):
    """The input data is not usable (wrong columns, missing values, not mirrored, ...)."""


@dataclass(frozen=True)
class SourcedTransaction:
    transaction: TransactionIn
    source_ref: str | None = None      # e.g. 'row:2' for the CSV, a txid for real data


class TransactionSource(ABC):
    #: 'synthetic' or 'real_bitcoin'. Stored on every transaction and wallet from this source.
    source: str

    @abstractmethod
    def fetch(self) -> list[SourcedTransaction]:
        """Return the source's transactions, normalized and validated."""
