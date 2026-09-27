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
    network_observations_total: int = 0        # SYNTHETIC observations
    clusters_total: int = 0                    # synthetic clusters (real-data clusters are listed via /api/clusters?source=real_bitcoin)
    cases_total: int = 0
    active_cases: int = 0                      # cases that are not Closed


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
    clusters_total: int = 0
    cluster_error: str | None = None


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
    is_case: bool = False            # a lead is not a case; true only when an investigator has added it to a case
    case_ids: list[str] = Field(default_factory=list)
    review_status: str = "Unreviewed"    # Unreviewed / Under Review (investigator marker) / Case Created (derived)


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
    case_ids: list[str] = Field(default_factory=list)
    review_status: str = "Unreviewed"
    findings: list[FindingOut] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    fusion_note: str


# ---- network observations, clusters, graph -----------------------------------------------------------------
SYNTHETIC_NETWORK_NOTE = ("Synthetic network observation: demo data generated for the prototype (documentation IP ranges only). "
                          "It is not derived from the Bitcoin blockchain and does not identify any real person or device.")


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    observation_id: str
    transaction_id: str | None
    wallet_address: str
    observed_party: str | None = None          # 'sender' or 'receiver' (from the observation id)
    ip_address: str | None
    device_id: str | None
    user_agent: str | None
    network_type: str | None
    session_id: str | None
    geo_region: str | None
    observed_at: UtcDatetime | None
    origin: str
    is_synthetic: bool
    label: str = "Synthetic network observation"


class ObservationPage(BaseModel):
    total: int
    limit: int
    offset: int
    note: str = SYNTHETIC_NETWORK_NOTE
    items: list[ObservationOut]


class EntityDetail(BaseModel):
    entity_type: str
    entity_id: str
    observation_count: int
    wallets: list[str]
    transactions: int
    related: dict[str, list[str]]
    first_observed_at: UtcDatetime | None
    last_observed_at: UtcDatetime | None
    note: str = SYNTHETIC_NETWORK_NOTE


class NetworkImportOut(BaseModel):
    file: str
    rows_in_file: int
    inserted: int
    skipped_existing: int
    rejected: list[dict]
    observations_total: int
    clusters_total: int = 0
    generated: bool = False
    note: str = SYNTHETIC_NETWORK_NOTE


class NetworkImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generate: bool = False        # (re)generate the CSV from the imported transactions first
    replace: bool = False


class GraphNodeOut(BaseModel):
    id: str
    type: str
    label: str
    data: dict = Field(default_factory=dict)


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    type: str
    data: dict = Field(default_factory=dict)


class GraphOut(BaseModel):
    focus: dict
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    truncated: bool = False
    omitted: dict[str, int] = Field(default_factory=dict)
    counts: dict[str, dict[str, int]]
    notes: list[str]


class ClusterOut(BaseModel):
    cluster_id: str
    method: str
    method_label: str
    source: str
    entity_count: int
    wallet_count: int
    transaction_count: int
    network_observation_count: int
    priority_summary: dict | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ClusterPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ClusterOut]


class ClusterWallet(BaseModel):
    wallet_address: str
    is_lead: bool
    ml_prediction: str | None = None
    priority_level: str | None = None
    priority_rank: int | None = None
    combined_score: float | None = None
    transaction_count: int


class ClusterRelationship(BaseModel):
    source: str
    target: str
    transfers: int
    total_btc: float


class ClusterDetail(ClusterOut):
    wallets: list[ClusterWallet]
    devices: list[str]
    ip_addresses: list[str]
    sessions: list[str]
    internal_relationships: list[ClusterRelationship]
    graph: GraphOut
    case_ids: list[str] = Field(default_factory=list)
    note: str


# ---- cases ---------------------------------------------------------------------------------------------------
from typing import Literal  # noqa: E402

CaseStatus = Literal["Open", "Under investigation", "Closed"]
CasePriority = Literal["High", "Medium", "Low"]
ItemType = Literal["lead", "wallet", "transaction", "cluster", "entity"]
CASE_DISCLAIMER = ("A case records an investigator's decision to review something formally. It does not establish that any wrongdoing "
                   "occurred; the evidence in it is behavioural and statistical and requires investigator judgement.")


class CaseItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: ItemType
    id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


class CaseCreate(BaseModel):
    """Creating a case is always an explicit investigator action; nothing creates one automatically."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: CasePriority | None = None
    assigned_to: str | None = Field(default=None, max_length=80)
    items: list[CaseItemIn] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=5000)


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    assigned_to: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _something_to_change(self) -> "CaseUpdate":
        if not self.model_fields_set:
            raise ValueError("Give at least one field to change.")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be empty.")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be empty.")
        return self


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=5000)


class ReviewedIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_type: ItemType
    item_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


class CaseHistoryOut(BaseModel):
    entry_id: int
    at: UtcDatetime
    actor: str
    action: str
    detail: str | None


class CaseItemOut(BaseModel):
    item_type: str
    item_id: str
    added_at: UtcDatetime
    evidence_snapshot: dict | None            # the evidence as it stood when the item was added
    current: dict | None = None               # today's values, so any change since then is visible


class CaseSummary(BaseModel):
    case_id: str
    title: str
    status: str
    priority: str | None
    assigned_to: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    item_counts: dict[str, int]


class CasePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CaseSummary]


class CaseDetail(CaseSummary):
    description: str | None
    items: list[CaseItemOut]
    history: list[CaseHistoryOut]
    notes: list[CaseHistoryOut]
    wallets: list[str]
    related_transactions: dict
    disclaimer: str = CASE_DISCLAIMER


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["Unreviewed", "Under Review"]


class ReviewOut(BaseModel):
    wallet_address: str
    status: str
    case_ids: list[str]


# ---- search --------------------------------------------------------------------------------------------------
class SearchHit(BaseModel):
    type: str
    id: str
    label: str
    subtitle: str | None = None
    match: str                              # what matched, e.g. "wallet address", "member wallet"
    data: dict = Field(default_factory=dict)


class SearchCategory(BaseModel):
    total: int
    items: list[SearchHit]


class SearchOut(BaseModel):
    query: str
    total: int
    categories: dict[str, SearchCategory]
    notes: list[str]


# ---------------------------------------------------------------------------------------------
# Bulk analysis (one call gives the frontend everything it needs to list and chart the whole population)
# ---------------------------------------------------------------------------------------------
class BulkWallet(BaseModel):
    wallet_address: str
    ml_score: float
    ml_prediction: str
    forensic_score: float
    forensic_rule_count: int
    evidence_level: str
    triggered_rules: list[str]                    # rule ids, in the order the pipeline reported them
    findings: list[str]                           # the rules' own explanation sentences (same order)
    combined_score: float
    priority_rank: int
    priority_level: str
    is_lead: bool
    reasons: list[str] = Field(default_factory=list)      # why it is a lead (leads only)
    case_ids: list[str] = Field(default_factory=list)
    review_status: str = "Unreviewed"
    features: dict[str, float]


class BulkAnalysis(BaseModel):
    run_id: int | None
    source: str
    finished_at: UtcDatetime | None
    stale: bool = False
    forensic_weight: float | None = None
    ml_weight: float | None = None
    fusion_note: str
    scored_wallets: int
    unscored_wallets: int
    wallets: list[BulkWallet]
