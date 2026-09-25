"""Replace/clear helpers. Only ever touch rows of one data source; cases are never deleted here."""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AnalysisRun, AnomalyResult, EntityCluster, EntityClusterMember, ForensicFinding, FusionResult, InvestigativeLead,
    NetworkObservation, Transaction, Wallet, WalletFeatures,
)


def purge_source(session: Session, source: str) -> int:
    """
    Delete every transaction, wallet and derived result of one source, in dependency order.
    Returns the number of transactions removed. Cases and their history are left untouched.
    """
    tx_ids = select(Transaction.transaction_id).where(Transaction.source == source)
    addresses = select(Wallet.address).where(Wallet.source == source)
    n_tx = len(session.scalars(tx_ids).all())

    session.execute(delete(NetworkObservation).where(or_(
        NetworkObservation.transaction_id.in_(tx_ids), NetworkObservation.wallet_address.in_(addresses))))
    for model in (ForensicFinding, FusionResult, AnomalyResult, WalletFeatures):
        session.execute(delete(model).where(model.wallet_address.in_(addresses)))
    session.execute(delete(InvestigativeLead).where(InvestigativeLead.source == source))
    clusters = select(EntityCluster.cluster_id).where(EntityCluster.source == source)
    session.execute(delete(EntityClusterMember).where(EntityClusterMember.cluster_id.in_(clusters)))
    session.execute(delete(EntityCluster).where(EntityCluster.source == source))
    session.execute(delete(AnalysisRun).where(AnalysisRun.source == source))
    session.execute(delete(Transaction).where(Transaction.source == source))
    session.execute(delete(Wallet).where(Wallet.source == source))
    return n_tx
