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
    # from the latest completed analysis (null until one has run)
    last_analysis_at: UtcDatetime | None = None
    analysis_stale: bool = False
    anomalous_wallets: int | None = None
    leads_total: int | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- analysis, leads --------------------------------------------------------------------------------
class AnalysisRunOut(BaseModel):
    run_id: int
    source: str
    trigger: str
    status: str
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    transfer_count: int | None
    wallet_count: int | None
    error: str | None = None
    run_config: dict | None = None


class RunSummaryOut(BaseModel):
    run_id: int
    status: str
    source: str
    trigger: str
    transfer_count: int
    wallet_count: int
    scored_wallets: int
    unscored_wallets: int
    anomalous_wallets: int
    leads_total: int
    new_leads: int
    duration_seconds: float
    error: str | None = None


class FindingOut(BaseModel):
    rule_id: str
    rule_name: str
    severity: str | None = None
    feature: str | None
    feature_value: float | None
    threshold: float | None
    evidence: str | None


class LeadOut(BaseModel):
    wallet_address: str
    source: str
    priority_rank: int
    priority_level: str
    combined_score: float
    ml_score: float
    ml_prediction: str
    forensic_score: float
    forensic_rule_count: int
    evidence_level: str
    reasons: list[str]
    first_flagged_at: UtcDatetime | None
    updated_at: UtcDatetime
    run_id: int
    is_case: bool = False            # a lead is not a case; becomes true only when an investigator opens one (checkpoint 5)


class LeadPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LeadOut]


class RelatedWallet(BaseModel):
    wallet_address: str
    transfers: int
    sent_to: int
    received_from: int
    total_btc: float
    ml_prediction: str | None = None


class LeadDetail(LeadOut):
    contributing_evidence: dict
    findings: list[FindingOut]
    features: dict[str, float]
    related_transactions: TransactionPage
    related_wallets: list[RelatedWallet]
    disclaimer: str


class WalletAnalysis(BaseModel):
    """Analysis of any wallet in the latest run (a lead or not)."""

    wallet_address: str
    scored: bool
    unscored_reason: str | None = None
    run_id: int | None = None
    ml_score: float | None = None
    ml_prediction: str | None = None
    forensic_score: float | None = None
    forensic_rule_count: int | None = None
    evidence_level: str | None = None
    combined_score: float | None = None
    priority_rank: int | None = None
    priority_level: str | None = None
    is_lead: bool = False
    findings: list[FindingOut] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    fusion_note: str
