"""
Database schema (SQLAlchemy 2.x).

Populated so far: `wallets`, `transactions` (Checkpoint 2); `analysis_runs`, `wallet_features`, `anomaly_results`,
`fusion_results`, `forensic_findings`, `investigative_leads` (Checkpoint 3).
Defined now, filled by later checkpoints (schema only until then): `network_observations`,
`analysis_runs`, `wallet_features`, `anomaly_results`, `fusion_results`, `forensic_findings`, `investigative_leads`,
`entity_clusters`, `entity_cluster_members` (Checkpoint 4); `wallet_reviews`, `cases`, `case_items`, `case_history` (Checkpoint 5).

Every transaction and wallet carries a `source` ('synthetic' or 'real_bitcoin') so the two are never
mixed silently. All timestamps are naive UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SOURCES = ("synthetic", "real_bitcoin")

# The 18 engineered wallet features from ml/feature_engineering.py (checked against it by a test).
FEATURE_COLUMNS = [
    "transaction_count", "incoming_count", "outgoing_count",
    "total_received_btc", "total_sent_btc", "avg_transaction_amount",
    "time_since_previous_tx", "avg_transaction_interval", "transaction_frequency",
    "dormancy_duration", "activity_burst",
    "incoming_outgoing_ratio", "fan_in", "fan_out",
    "unique_counterparties", "wallet_degree", "repeated_connections", "hop_distance",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------------------------
# Core data
# --------------------------------------------------------------------------------------------
class Wallet(Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (CheckConstraint("source IN ('synthetic', 'real_bitcoin')", name="ck_wallet_source"),)


class Transaction(Base):
    """One transfer sender -> receiver. (The raw CSV stores it as two mirrored rows; here it is one.)"""

    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    sender_wallet: Mapped[str] = mapped_column(ForeignKey("wallets.address"), index=True)
    receiver_wallet: Mapped[str] = mapped_column(ForeignKey("wallets.address"), index=True)
    amount_btc: Mapped[float] = mapped_column(Float)
    input_count: Mapped[int] = mapped_column(Integer, default=1)
    output_count: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(16), index=True)
    # Where it came from: 'row:<line>' for the CSV, 'stream:<n>' for the synthetic stream, the txid for
    # real data; NULL for transactions posted through the API.
    source_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_transaction_source_ref"),
        CheckConstraint("amount_btc > 0", name="ck_transaction_amount"),
        CheckConstraint("sender_wallet <> receiver_wallet", name="ck_transaction_distinct_parties"),
        CheckConstraint("source IN ('synthetic', 'real_bitcoin')", name="ck_transaction_source"),
    )


class NetworkObservation(Base):
    """
    SYNTHETIC network observation (an IP / device / session seen alongside a transaction).
    Never derived from the blockchain; NULL for real Bitcoin transactions unless a separate,
    legitimate observation source is configured.
    """

    __tablename__ = "network_observations"

    observation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.transaction_id"), index=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), index=True)       # documentation ranges only
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    network_type: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    geo_region: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    origin: Mapped[str] = mapped_column(String(48), default="synthetic_network_observation")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------------------------
# Analysis results (written by the analysis pipeline; one run scores one source's population)
# --------------------------------------------------------------------------------------------
class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")   # manual / monitor / import
    status: Mapped[str] = mapped_column(String(16), default="running")   # running / completed / failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    transfer_count: Mapped[int | None] = mapped_column(Integer)
    wallet_count: Mapped[int | None] = mapped_column(Integer)
    model_config_json: Mapped[dict | None] = mapped_column(JSON)   # Isolation Forest settings, fusion weights
    error: Mapped[str | None] = mapped_column(Text)


class WalletFeatures(Base):
    __tablename__ = "wallet_features"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.run_id"), primary_key=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), primary_key=True)
    transaction_count: Mapped[float | None] = mapped_column(Float)
    incoming_count: Mapped[float | None] = mapped_column(Float)
    outgoing_count: Mapped[float | None] = mapped_column(Float)
    total_received_btc: Mapped[float | None] = mapped_column(Float)
    total_sent_btc: Mapped[float | None] = mapped_column(Float)
    avg_transaction_amount: Mapped[float | None] = mapped_column(Float)
    time_since_previous_tx: Mapped[float | None] = mapped_column(Float)
    avg_transaction_interval: Mapped[float | None] = mapped_column(Float)
    transaction_frequency: Mapped[float | None] = mapped_column(Float)
    dormancy_duration: Mapped[float | None] = mapped_column(Float)
    activity_burst: Mapped[float | None] = mapped_column(Float)
    incoming_outgoing_ratio: Mapped[float | None] = mapped_column(Float)
    fan_in: Mapped[float | None] = mapped_column(Float)
    fan_out: Mapped[float | None] = mapped_column(Float)
    unique_counterparties: Mapped[float | None] = mapped_column(Float)
    wallet_degree: Mapped[float | None] = mapped_column(Float)
    repeated_connections: Mapped[float | None] = mapped_column(Float)
    hop_distance: Mapped[float | None] = mapped_column(Float)


class AnomalyResult(Base):
    __tablename__ = "anomaly_results"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.run_id"), primary_key=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), primary_key=True)
    anomaly_score: Mapped[float] = mapped_column(Float)          # Isolation Forest, higher = more unusual
    anomaly_prediction: Mapped[str] = mapped_column(String(16))  # 'Anomalous' / 'Normal'


class FusionResult(Base):
    """Database form of data/fusion_results.csv: the combined result and priority of every scored wallet."""

    __tablename__ = "fusion_results"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.run_id"), primary_key=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), primary_key=True)
    forensic_score: Mapped[float] = mapped_column(Float)          # rule_count / total rules (ml/result_fusion.py)
    forensic_rule_count: Mapped[int] = mapped_column(Integer)
    evidence_level: Mapped[str] = mapped_column(String(64))
    combined_score: Mapped[float] = mapped_column(Float)          # prototype weighting, not validated
    priority_rank: Mapped[int] = mapped_column(Integer, index=True)
    priority_level: Mapped[str] = mapped_column(String(16))


class ForensicFinding(Base):
    """One triggered forensic rule for one wallet in one run. The existing rules carry no severity."""

    __tablename__ = "forensic_findings"

    finding_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.run_id"), index=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_name: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str | None] = mapped_column(String(16))   # unused: the existing rules define none
    feature: Mapped[str | None] = mapped_column(String(64))
    feature_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[str | None] = mapped_column(Text)         # the rule's own explanation sentence
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InvestigativeLead(Base):
    """
    Current state of a wallet as an investigative LEAD. A lead is not a case: an investigator
    decides whether to open a case from it.
    """

    __tablename__ = "investigative_leads"

    lead_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), unique=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.run_id"))     # run that produced these numbers
    ml_score: Mapped[float | None] = mapped_column(Float)
    ml_prediction: Mapped[str | None] = mapped_column(String(16))
    forensic_score: Mapped[float | None] = mapped_column(Float)
    forensic_rule_count: Mapped[int | None] = mapped_column(Integer)
    evidence_level: Mapped[str | None] = mapped_column(String(64))
    combined_score: Mapped[float | None] = mapped_column(Float)
    priority_rank: Mapped[int | None] = mapped_column(Integer, index=True)
    priority_level: Mapped[str | None] = mapped_column(String(16))
    contributing_evidence: Mapped[dict | None] = mapped_column(JSON)
    first_flagged_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_lead_source_rank", "source", "priority_rank"),)


# --------------------------------------------------------------------------------------------
# Related-entity clusters
# --------------------------------------------------------------------------------------------
class EntityCluster(Base):
    __tablename__ = "entity_clusters"

    cluster_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(48))     # 'shared_network_observation' / 'transaction_community'
    source: Mapped[str] = mapped_column(String(16), index=True)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    wallet_count: Mapped[int] = mapped_column(Integer, default=0)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    network_observation_count: Mapped[int] = mapped_column(Integer, default=0)
    priority_summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    members: Mapped[list["EntityClusterMember"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")


class EntityClusterMember(Base):
    __tablename__ = "entity_cluster_members"

    cluster_id: Mapped[str] = mapped_column(ForeignKey("entity_clusters.cluster_id"), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(24), primary_key=True)   # wallet / transaction / ip / device / session
    entity_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    cluster: Mapped[EntityCluster] = relationship(back_populates="members")


# --------------------------------------------------------------------------------------------
# Cases (created only by an investigator)
# --------------------------------------------------------------------------------------------
class WalletReview(Base):
    """
    An investigator marked a wallet 'Under Review'. (Absent = Unreviewed. 'Case Created' is not stored: it is derived from
    the wallet being an item of a case.) Purely a workflow marker; it never changes any analysis result.
    """

    __tablename__ = "wallet_reviews"

    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="Under Review")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Case(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(32), primary_key=True)          # CASE-0001
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="Open")
    priority: Mapped[str | None] = mapped_column(String(16))
    assigned_to: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items: Mapped[list["CaseItem"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    history: Mapped[list["CaseHistory"]] = relationship(back_populates="case", cascade="all, delete-orphan", order_by="CaseHistory.entry_id")


class CaseItem(Base):
    __tablename__ = "case_items"

    item_pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))     # lead / wallet / transaction / cluster
    item_id: Mapped[str] = mapped_column(String(128))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSON)   # evidence as it stood when added

    case: Mapped[Case] = relationship(back_populates="items")

    __table_args__ = (UniqueConstraint("case_id", "item_type", "item_id", name="uq_case_item"),)


class CaseHistory(Base):
    __tablename__ = "case_history"

    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(80), default="Investigator")
    action: Mapped[str] = mapped_column(String(48))     # case_created / wallet_added / note_added / status_changed ...
    detail: Mapped[str | None] = mapped_column(Text)

    case: Mapped[Case] = relationship(back_populates="history")
