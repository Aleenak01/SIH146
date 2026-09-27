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
from typing import Any

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


class AppSetting(Base):
    """One persisted local setting (monitoring on/off, interval, ...). Values are small JSON scalars; never credentials."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------------------------
# Rich transaction model (Phase 1). All additive: the tables above are untouched. Everything here is SYNTHETIC.
# --------------------------------------------------------------------------------------------
RICH_SCRIPT_TYPES = ("p2pkh", "p2sh", "p2wpkh", "p2wsh", "p2tr")


class TxDetails(Base):
    """Address-level detail of one transaction: its txid, fee and script type. The inputs, outputs and network flow hang off it."""

    __tablename__ = "tx_details"

    detail_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"), unique=True)
    txid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fee_btc: Mapped[float] = mapped_column(Float)
    script_type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16), default="synthetic")

    __table_args__ = (
        CheckConstraint("fee_btc >= 0", name="ck_tx_details_fee"),
        CheckConstraint("script_type IN ('p2pkh', 'p2sh', 'p2wpkh', 'p2wsh', 'p2tr')", name="ck_tx_details_script"),
    )


class TxInput(Base):
    __tablename__ = "tx_inputs"

    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    amount_btc: Mapped[float] = mapped_column(Float)

    __table_args__ = (CheckConstraint("amount_btc > 0", name="ck_tx_inputs_amount"),)


class TxOutput(Base):
    __tablename__ = "tx_outputs"

    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    amount_btc: Mapped[float] = mapped_column(Float)

    __table_args__ = (CheckConstraint("amount_btc > 0", name="ck_tx_outputs_amount"),)


class FlowRecord(Base):
    """
    SYNTHETIC network flow of a transaction: which (randomly assigned) client IP sent it to which (synthetic) node IP and on
    which ports. geo_country / asn / asn_org describe src_ip (DB-IP Lite lookup). Not observed traffic.
    """

    __tablename__ = "flow_records"

    flow_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"), unique=True)
    src_ip: Mapped[str | None] = mapped_column(String(64), index=True)
    dst_ip: Mapped[str | None] = mapped_column(String(64), index=True)
    src_port: Mapped[int | None] = mapped_column(Integer)
    dst_port: Mapped[int | None] = mapped_column(Integer)
    geo_country: Mapped[str | None] = mapped_column(String(2))
    asn: Mapped[int | None] = mapped_column(Integer, index=True)
    asn_org: Mapped[str | None] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(48), default="synthetic_flow_record")


# --------------------------------------------------------------------------------------------
# Address entities and network correlation (Phase 2). All additive: nothing above is touched. Built entirely from the
# rich address-level tables above (tx_inputs/tx_outputs/tx_details/flow_records); never from the flat wallet-level view.
# --------------------------------------------------------------------------------------------
class AddressEntity(Base):
    """
    A group of addresses the common-input-ownership heuristic believes share one controller, because they were spent
    together as inputs of one transaction. Indicates likely common control, never proof (see analysis/entities.py).
    """

    __tablename__ = "address_entities"

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)      # 'CIO-<lowest address>'
    method: Mapped[str] = mapped_column(String(48))                          # 'common_input_ownership'
    source: Mapped[str] = mapped_column(String(16), index=True)
    address_count: Mapped[int] = mapped_column(Integer, default=0)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    total_sent_btc: Mapped[float] = mapped_column(Float, default=0.0)
    total_received_btc: Mapped[float] = mapped_column(Float, default=0.0)
    distinct_ip_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_asn_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_country_count: Mapped[int] = mapped_column(Integer, default=0)
    countries: Mapped[list | None] = mapped_column(JSON)
    asns: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    members: Mapped[list["AddressEntityMember"]] = relationship(back_populates="entity", cascade="all, delete-orphan")


class AddressEntityMember(Base):
    __tablename__ = "address_entity_members"

    entity_id: Mapped[str] = mapped_column(ForeignKey("address_entities.entity_id"), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)

    entity: Mapped[AddressEntity] = relationship(back_populates="members")


class EntityIPLink(Base):
    """Correlation: an entity spent from an address whose transaction's SYNTHETIC network flow used this src_ip."""

    __tablename__ = "entity_ip_links"

    entity_id: Mapped[str] = mapped_column(ForeignKey("address_entities.entity_id"), primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    asn: Mapped[int | None] = mapped_column(Integer, index=True)
    geo_country: Mapped[str | None] = mapped_column(String(2))
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class EntityLink(Base):
    """Correlation: two entities whose spending transactions were seen from the same SYNTHETIC src_ip. Not proof of a link."""

    __tablename__ = "entity_links"

    entity_a: Mapped[str] = mapped_column(ForeignKey("address_entities.entity_id"), primary_key=True)
    entity_b: Mapped[str] = mapped_column(ForeignKey("address_entities.entity_id"), primary_key=True)
    link_type: Mapped[str] = mapped_column(String(24), primary_key=True, default="shared_ip")
    shared_ip_count: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[int] = mapped_column(Integer, default=0)     # number of distinct shared synthetic IPs; used for ranking

    __table_args__ = (CheckConstraint("entity_a < entity_b", name="ck_entity_link_order"),)


class CorrelationFinding(Base):
    """
    A network<->blockchain correlation finding for one entity: EVIDENCE, never a verdict. `evidence` keeps the
    underlying txids/IPs/counts so an investigator can check it themselves.
    """

    __tablename__ = "correlation_findings"

    finding_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("address_entities.entity_id"), index=True)
    finding_type: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------------------------
# Pattern detectors (Phase 3, Part A): peeling chains and CoinJoin-like candidates. Additive; nothing above is
# touched. Peeling is built from the existing flat `transactions` table; CoinJoin detection is built from the rich
# address-level tables above. Both are heuristic flags -- investigative signals, never proof of anything.
# --------------------------------------------------------------------------------------------
class PeelingChain(Base):
    """
    A peeling chain: a sequence of wallet-level transfers A0 -> A1 -> ... -> An where each hop forwards most of what
    the previous hop just delivered (see analysis/peeling.py for the exact rule and its threshold). A heuristic
    pattern (large, repeated "change"), never proof of layering or laundering.
    """

    __tablename__ = "peeling_chains"

    chain_id: Mapped[str] = mapped_column(String(80), primary_key=True)     # 'PEEL-<the chain's first transaction_id>'
    source: Mapped[str] = mapped_column(String(16), index=True)
    start_wallet: Mapped[str] = mapped_column(String(128), index=True)
    end_wallet: Mapped[str] = mapped_column(String(128), index=True)
    hop_count: Mapped[int] = mapped_column(Integer)
    total_btc_start: Mapped[float] = mapped_column(Float)
    total_btc_end: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    hops: Mapped[list["PeelingChainHop"]] = relationship(back_populates="chain", cascade="all, delete-orphan", order_by="PeelingChainHop.hop_index")


class PeelingChainHop(Base):
    __tablename__ = "peeling_chain_hops"

    chain_id: Mapped[str] = mapped_column(ForeignKey("peeling_chains.chain_id"), primary_key=True)
    hop_index: Mapped[int] = mapped_column(Integer, primary_key=True)          # 1-based: hop 1 is the chain's first transfer
    from_wallet: Mapped[str] = mapped_column(String(128), index=True)
    to_wallet: Mapped[str] = mapped_column(String(128), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"))
    amount_btc: Mapped[float] = mapped_column(Float)

    chain: Mapped[PeelingChain] = relationship(back_populates="hops")


class CoinJoinCandidate(Base):
    """
    A transaction whose rich (address-level) data looks like a CoinJoin: several distinct input addresses spent
    together with several outputs of about the same value. A heuristic candidate flag, never a certainty (see
    analysis/coinjoin.py). Transactions flagged here are excluded from the common-input-ownership union-find in
    analysis/entities.py, since pooling several independent people's inputs is the whole point of a CoinJoin.
    """

    __tablename__ = "coinjoin_candidates"

    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    input_count: Mapped[int] = mapped_column(Integer)             # distinct input addresses
    output_count: Mapped[int] = mapped_column(Integer)            # distinct output addresses
    equal_output_group_size: Mapped[int] = mapped_column(Integer)  # size of the largest near-equal-value output group
    equal_output_value: Mapped[float] = mapped_column(Float)      # that group's representative (lowest) value
    score: Mapped[float] = mapped_column(Float)                   # 0-1 heuristic confidence; see analysis/coinjoin.py
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConfidenceScore(Base):
    """
    A second, explainable score per wallet (Phase 4 Part A), alongside (never replacing) fusion_results.combined_score.
    See analysis/confidence.py for the weighting and its reasoning; each contributing signal is its own row in
    ConfidenceSignal below, so an investigator can see exactly why the score is what it is.
    """

    __tablename__ = "confidence_scores"

    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float] = mapped_column(Float)                    # 0-1; see analysis/confidence.py
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConfidenceSignal(Base):
    __tablename__ = "confidence_signals"

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("confidence_scores.wallet_address"), index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    signal_name: Mapped[str] = mapped_column(String(64))
    contribution: Mapped[float] = mapped_column(Float)             # this signal's share of the score above
    detail: Mapped[str] = mapped_column(Text)                      # plain-language reason
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
