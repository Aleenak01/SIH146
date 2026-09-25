"""Pydantic schemas: request validation and JSON responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

WALLET_PATTERN = r"^[A-Za-z0-9_.:\-]+$"
MAX_BTC = 21_000_000


def _iso_z(value: datetime) -> str:
    return value.isoformat() + "Z"          # stored values are naive UTC


UtcDatetime = Annotated[datetime, PlainSerializer(_iso_z, return_type=str, when_used="json")]


class TransactionIn(BaseModel):
    """
    A transaction as accepted by the ingestion layer. This is also the normalized internal form that
    every TransactionSource (CSV, stream, real Bitcoin) must produce. Extra fields are rejected.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transaction_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:\-]+$")
    timestamp: datetime
    sender_wallet: str = Field(min_length=1, max_length=128, pattern=WALLET_PATTERN)
    receiver_wallet: str = Field(min_length=1, max_length=128, pattern=WALLET_PATTERN)
    amount_btc: float = Field(gt=0, le=MAX_BTC, allow_inf_nan=False)
    input_count: int = Field(default=1, ge=1, le=10_000)
    output_count: int = Field(default=1, ge=1, le=10_000)

    @field_validator("timestamp")
    @classmethod
    def _to_naive_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    @field_validator("amount_btc")
    @classmethod
    def _satoshi_precision(cls, v: float) -> float:
        v = round(v, 8)
        if v < 1e-8:
            raise ValueError("amount_btc must be at least 1 satoshi (0.00000001)")
        return v

    @model_validator(mode="after")
    def _distinct_parties(self) -> "TransactionIn":
        if self.sender_wallet == self.receiver_wallet:
            raise ValueError("sender_wallet and receiver_wallet must differ")
        return self


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    timestamp: UtcDatetime
    sender_wallet: str
    receiver_wallet: str
    amount_btc: float
    input_count: int
    output_count: int
    source: str
    source_ref: str | None = None
    created_at: UtcDatetime


class TransactionPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TransactionOut]


class WalletOut(BaseModel):
    address: str
    source: str
    first_seen: UtcDatetime | None
    last_seen: UtcDatetime | None
    transaction_count: int
    incoming_count: int
    outgoing_count: int
    total_received_btc: float
    total_sent_btc: float


class WalletDetail(WalletOut):
    unique_counterparties: int


class WalletPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[WalletOut]


class RejectedItem(BaseModel):
    index: int
    error: str


class IngestResult(BaseModel):
    inserted: int
    duplicates: int
    rejected: list[RejectedItem] = Field(default_factory=list)
    transaction_ids: list[str] = Field(default_factory=list)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replace: bool = False


class ImportResult(BaseModel):
    source: str
    file: str
    transfers_in_file: int
    inserted: int
    skipped_existing: int
    wallets_total: int
    replaced_existing: bool


class OverviewOut(BaseModel):
    transactions_total: int
    wallets_total: int
    by_source: dict[str, dict[str, int]]
    first_transaction_at: UtcDatetime | None
    last_transaction_at: UtcDatetime | None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
