"""
Geo aggregation (Phase 4 Part A): country/ASN breakdown of transaction network traffic, from
flow_records.geo_country/asn/asn_org (a synthetic, offline DB-IP Lite lookup against a randomly-assigned client IP
per transaction -- see analysis/entities.py's "Rules for later phases" note 2 in architecture.md). Read-only, no
new table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import FlowRecord, InvestigativeLead, Transaction, Wallet

GEO_NOTE = ("Synthetic GeoIP demo data: IP addresses are randomly assigned to transactions for the demo and are "
           "not observed network traffic. Country and ASN come from an offline lookup (DB-IP Lite) against those "
           "assigned IP addresses, not from anything a real person or network did.")


def wallet_geo(session: Session, wallet: str) -> dict[str, Any] | None:
    """Country/ASN breakdown of one wallet's transaction traffic (as sender or receiver). None if the wallet does not exist."""
    w = session.get(Wallet, wallet)
    if w is None:
        return None
    tx_ids = select(Transaction.transaction_id).where(or_(Transaction.sender_wallet == wallet, Transaction.receiver_wallet == wallet))
    rows = session.execute(select(FlowRecord).where(FlowRecord.transaction_id.in_(tx_ids))).scalars().all()

    countries: dict[str, int] = {}
    asns: dict[tuple[int, str | None], int] = {}
    for f in rows:
        if f.geo_country:
            countries[f.geo_country] = countries.get(f.geo_country, 0) + 1
        if f.asn:
            key = (f.asn, f.asn_org)
            asns[key] = asns.get(key, 0) + 1

    return {
        "wallet_address": wallet,
        "transaction_count": len(rows),
        "countries": [{"country": c, "count": n} for c, n in sorted(countries.items(), key=lambda x: (-x[1], x[0]))],
        "asns": [{"asn": a, "asn_org": org, "count": n} for (a, org), n in sorted(asns.items(), key=lambda x: (-x[1], x[0][0]))],
        "note": GEO_NOTE,
    }


def geo_summary(session: Session, *, source: str = "synthetic", top: int = 10) -> dict[str, Any]:
    """Dataset-wide top countries/ASNs across every wallet that is a current investigative lead (for a Dashboard panel)."""
    lead_wallets = select(InvestigativeLead.wallet_address).where(InvestigativeLead.source == source)
    tx_ids = select(Transaction.transaction_id).where(or_(Transaction.sender_wallet.in_(lead_wallets), Transaction.receiver_wallet.in_(lead_wallets)))

    country_rows = session.execute(
        select(FlowRecord.geo_country, func.count()).where(FlowRecord.transaction_id.in_(tx_ids), FlowRecord.geo_country.is_not(None))
        .group_by(FlowRecord.geo_country).order_by(func.count().desc(), FlowRecord.geo_country).limit(top)
    ).all()
    asn_rows = session.execute(
        select(FlowRecord.asn, FlowRecord.asn_org, func.count()).where(FlowRecord.transaction_id.in_(tx_ids), FlowRecord.asn.is_not(None))
        .group_by(FlowRecord.asn, FlowRecord.asn_org).order_by(func.count().desc(), FlowRecord.asn).limit(top)
    ).all()
    lead_count = session.scalar(select(func.count()).select_from(InvestigativeLead).where(InvestigativeLead.source == source)) or 0

    return {
        "source": source, "lead_count": lead_count,
        "top_countries": [{"country": c, "count": n} for c, n in country_rows],
        "top_asns": [{"asn": a, "asn_org": org, "count": n} for a, org, n in asn_rows],
        "note": GEO_NOTE,
    }
