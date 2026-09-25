"""Queries behind the leads and wallet-analysis endpoints."""

from __future__ import annotations

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..analysis.fusion import FUSION_LABEL
from ..analysis.service import latest_run
from ..models import (
    FEATURE_COLUMNS, AnomalyResult, ForensicFinding, FusionResult, InvestigativeLead, Transaction, Wallet, WalletFeatures,
)
from ..schemas import (
    FindingOut, LeadDetail, LeadOut, RelatedWallet, TransactionOut, TransactionPage, WalletAnalysis,
)
from . import case_links, queries

DISCLAIMER = ("This wallet is an investigative lead, not a case and not evidence of criminal activity. Anomalous behavior and "
              "triggered rules are indicators that require investigator review.")

LEAD_SORTS = {
    "priority_rank": InvestigativeLead.priority_rank,
    "combined_score": InvestigativeLead.combined_score,
    "ml_score": InvestigativeLead.ml_score,
    "first_flagged_at": InvestigativeLead.first_flagged_at,
    "wallet_address": InvestigativeLead.wallet_address,
}


def _lead_out(l: InvestigativeLead, cases: dict[str, list[str]] | None = None, review: dict[str, str] | None = None) -> LeadOut:
    case_ids = (cases or {}).get(l.wallet_address, [])
    return LeadOut(
        case_ids=case_ids, is_case=bool(case_ids), review_status=(review or {}).get(l.wallet_address, "Unreviewed"),
        wallet_address=l.wallet_address, source=l.source, priority_rank=l.priority_rank, priority_level=l.priority_level,
        combined_score=l.combined_score, ml_score=l.ml_score, ml_prediction=l.ml_prediction, forensic_score=l.forensic_score,
        forensic_rule_count=l.forensic_rule_count, evidence_level=l.evidence_level,
        reasons=(l.contributing_evidence or {}).get("reasons", []), first_flagged_at=l.first_flagged_at, updated_at=l.updated_at, run_id=l.run_id,
    )


def list_leads(session: Session, *, q, priority_level, ml_prediction, min_score, source, sort, order, limit, offset):
    clauses = []
    if q:
        clauses.append(InvestigativeLead.wallet_address.contains(q, autoescape=True))
    if priority_level:
        clauses.append(InvestigativeLead.priority_level == priority_level)
    if ml_prediction:
        clauses.append(InvestigativeLead.ml_prediction == ml_prediction)
    if min_score is not None:
        clauses.append(InvestigativeLead.combined_score >= min_score)
    if source:
        clauses.append(InvestigativeLead.source == source)
    where = and_(*clauses) if clauses else None

    count = select(func.count()).select_from(InvestigativeLead)
    stmt = select(InvestigativeLead)
    if where is not None:
        count, stmt = count.where(where), stmt.where(where)
    col = LEAD_SORTS[sort]
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), InvestigativeLead.wallet_address.asc()).limit(limit).offset(offset)
    leads = list(session.scalars(stmt))
    wallets = [l.wallet_address for l in leads]
    cases = case_links.case_ids_for_wallets(session, wallets)
    review = case_links.review_status(session, wallets, cases)
    return session.scalar(count) or 0, [_lead_out(l, cases, review) for l in leads]


def _findings(session: Session, run_id: int, wallet: str) -> list[FindingOut]:
    rows = session.scalars(select(ForensicFinding).where(ForensicFinding.run_id == run_id, ForensicFinding.wallet_address == wallet).order_by(ForensicFinding.finding_id))
    return [FindingOut(rule_id=f.rule_id, rule_name=f.rule_name, severity=f.severity, feature=f.feature, feature_value=f.feature_value,
                       threshold=f.threshold, evidence=f.evidence) for f in rows]


def _features(session: Session, run_id: int, wallet: str) -> dict[str, float]:
    row = session.get(WalletFeatures, (run_id, wallet))
    return {} if row is None else {c: getattr(row, c) for c in FEATURE_COLUMNS}


def related_wallets(session: Session, wallet: str, limit: int = 10) -> list[RelatedWallet]:
    """Direct counterparties by number of transfers (from the transactions table)."""
    other = case((Transaction.sender_wallet == wallet, Transaction.receiver_wallet), else_=Transaction.sender_wallet)
    stmt = (
        select(other.label("w"), func.count().label("n"),
               func.sum(case((Transaction.sender_wallet == wallet, 1), else_=0)).label("sent"),
               func.sum(case((Transaction.receiver_wallet == wallet, 1), else_=0)).label("received"),
               func.sum(Transaction.amount_btc).label("btc"))
        .where(or_(Transaction.sender_wallet == wallet, Transaction.receiver_wallet == wallet))
        .group_by("w").order_by(func.count().desc(), func.sum(Transaction.amount_btc).desc(), "w").limit(limit)
    )
    rows = session.execute(stmt).all()
    predictions = dict(session.execute(select(InvestigativeLead.wallet_address, InvestigativeLead.ml_prediction).where(InvestigativeLead.wallet_address.in_([r.w for r in rows]))).all()) if rows else {}
    return [RelatedWallet(wallet_address=r.w, transfers=r.n, sent_to=int(r.sent), received_from=int(r.received), total_btc=round(r.btc, 8),
                          ml_prediction=predictions.get(r.w)) for r in rows]


def get_lead_detail(session: Session, wallet: str, related_limit: int = 10) -> LeadDetail | None:
    lead = session.scalars(select(InvestigativeLead).where(InvestigativeLead.wallet_address == wallet)).first()
    if lead is None:
        return None
    clauses = queries.transaction_filters(wallet=wallet)
    total, txs = queries.list_transactions(session, clauses, sort="timestamp", order="desc", limit=related_limit, offset=0)
    cases = case_links.case_ids_for_wallets(session, [wallet])
    return LeadDetail(
        **_lead_out(lead, cases, case_links.review_status(session, [wallet], cases)).model_dump(), contributing_evidence=lead.contributing_evidence or {},
        findings=_findings(session, lead.run_id, wallet), features=_features(session, lead.run_id, wallet),
        related_transactions=TransactionPage(total=total, limit=related_limit, offset=0, items=[TransactionOut.model_validate(t) for t in txs]),
        related_wallets=related_wallets(session, wallet), disclaimer=DISCLAIMER,
    )


def wallet_analysis(session: Session, wallet: str) -> WalletAnalysis | None:
    """Latest-run analysis of any wallet. Returns None if the wallet does not exist."""
    if session.get(Wallet, wallet) is None:
        return None
    run = latest_run(session, session.get(Wallet, wallet).source)
    if run is None:
        return _unscored(session, wallet, "No analysis has run yet.", None)
    ml = session.get(AnomalyResult, (run.run_id, wallet))
    if ml is None:
        reason = ((run.model_config_json or {}).get("unscored_examples") or {}).get(wallet) or "Not scored in the latest analysis (fewer than 2 transactions or incomplete features)."
        return _unscored(session, wallet, reason, run.run_id)
    fusion = session.get(FusionResult, (run.run_id, wallet))
    is_lead = session.scalar(select(func.count()).select_from(InvestigativeLead).where(InvestigativeLead.wallet_address == wallet)) > 0
    cases = case_links.case_ids_for_wallets(session, [wallet])
    return WalletAnalysis(
        case_ids=cases.get(wallet, []), review_status=case_links.review_status(session, [wallet], cases)[wallet],
        wallet_address=wallet, scored=True, run_id=run.run_id, ml_score=ml.anomaly_score, ml_prediction=ml.anomaly_prediction,
        forensic_score=fusion.forensic_score, forensic_rule_count=fusion.forensic_rule_count, evidence_level=fusion.evidence_level,
        combined_score=fusion.combined_score, priority_rank=fusion.priority_rank, priority_level=fusion.priority_level, is_lead=is_lead,
        findings=_findings(session, run.run_id, wallet), features=_features(session, run.run_id, wallet), fusion_note=FUSION_LABEL,
    )


def _unscored(session: Session, wallet: str, reason: str, run_id: int | None) -> WalletAnalysis:
    cases = case_links.case_ids_for_wallets(session, [wallet])
    return WalletAnalysis(wallet_address=wallet, scored=False, unscored_reason=reason, run_id=run_id, fusion_note=FUSION_LABEL,
                          case_ids=cases.get(wallet, []), review_status=case_links.review_status(session, [wallet], cases)[wallet])
