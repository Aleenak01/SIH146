"""Queries behind the entity endpoints (Phase 2: common-input-ownership entities + network correlation)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.entities import METHOD_LABEL, NOTE
from ..models import AddressEntity, AddressEntityMember, CorrelationFinding, EntityIPLink, EntityLink, Transaction, TxInput


def _entity_out(e: AddressEntity) -> dict[str, Any]:
    return {
        "entity_id": e.entity_id, "method": e.method, "method_label": METHOD_LABEL, "source": e.source,
        "address_count": e.address_count, "transaction_count": e.transaction_count,
        "first_seen": e.first_seen, "last_seen": e.last_seen,
        "total_sent_btc": e.total_sent_btc, "total_received_btc": e.total_received_btc,
        "distinct_ip_count": e.distinct_ip_count, "distinct_asn_count": e.distinct_asn_count, "distinct_country_count": e.distinct_country_count,
        "countries": e.countries or [], "asns": e.asns or [],
        "created_at": e.created_at, "updated_at": e.updated_at,
    }


def entity_ids_for_wallet(session: Session, wallet: str) -> set[str]:
    """
    Entity ids with at least one address that was an INPUT of a transaction sent by `wallet` (the flat, wallet-level
    view). This is a read-only, presentational cross-reference for the investigator -- entities themselves are
    always built without ever reading sender_wallet/receiver_wallet (see analysis/entities.py); this function is not
    part of that computation and does not feed back into it. Used by list_entities' `wallet` filter and by
    services/typology.py.
    """
    tx_ids = select(Transaction.transaction_id).where(Transaction.sender_wallet == wallet)
    addrs = select(TxInput.address).where(TxInput.transaction_id.in_(tx_ids))
    return set(session.scalars(select(AddressEntityMember.entity_id).where(AddressEntityMember.address.in_(addrs))))


def linked_wallets_for_entities(session: Session, entity_ids: list[str]) -> dict[str, list[str]]:
    """{entity_id: [wallets that sent a transaction the entity spent from]}, for a bounded set of entities (a page)."""
    if not entity_ids:
        return {}
    members = session.execute(select(AddressEntityMember.entity_id, AddressEntityMember.address).where(AddressEntityMember.entity_id.in_(entity_ids))).all()
    addr_to_entities: dict[str, set[str]] = defaultdict(set)
    for eid, addr in members:
        addr_to_entities[addr].add(eid)
    tx_rows = session.execute(select(TxInput.transaction_id, TxInput.address).where(TxInput.address.in_(addr_to_entities))).all()
    entity_tx: dict[str, set[str]] = defaultdict(set)
    for tid, addr in tx_rows:
        for eid in addr_to_entities[addr]:
            entity_tx[eid].add(tid)
    all_tx_ids = {t for tids in entity_tx.values() for t in tids}
    tx_wallet = dict(session.execute(select(Transaction.transaction_id, Transaction.sender_wallet).where(Transaction.transaction_id.in_(all_tx_ids))).all())
    return {eid: sorted({tx_wallet[t] for t in tids if t in tx_wallet}) for eid, tids in entity_tx.items()}


def list_entities(session: Session, *, source: str | None, min_addresses: int | None, country: str | None, asn: int | None,
                  ip: str | None, wallet: str | None, q: str | None, sort: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    stmt = select(AddressEntity)
    if source:
        stmt = stmt.where(AddressEntity.source == source)
    if min_addresses:
        stmt = stmt.where(AddressEntity.address_count >= min_addresses)
    entities = list(session.scalars(stmt))

    if country:
        country = country.upper()
        entities = [e for e in entities if country in (e.countries or [])]
    if asn is not None:
        entities = [e for e in entities if asn in (e.asns or [])]
    if ip:
        ip_entities = set(session.scalars(select(EntityIPLink.entity_id).where(EntityIPLink.ip_address == ip)))
        entities = [e for e in entities if e.entity_id in ip_entities]
    if wallet:
        wallet_entities = entity_ids_for_wallet(session, wallet)
        entities = [e for e in entities if e.entity_id in wallet_entities]
    if q:
        q_lower = q.strip().lower()
        member_entities = set(session.scalars(select(AddressEntityMember.entity_id).where(AddressEntityMember.address.contains(q, autoescape=True))))
        entities = [e for e in entities if q_lower in e.entity_id.lower() or e.entity_id in member_entities]

    keys = {
        "size": lambda e: (-e.address_count, -e.transaction_count, e.entity_id),
        "activity": lambda e: (-(e.total_sent_btc + e.total_received_btc), e.entity_id),
        "id": lambda e: e.entity_id,
    }
    entities.sort(key=keys.get(sort, keys["size"]))
    total = len(entities)
    page = entities[offset: offset + limit]
    linked = linked_wallets_for_entities(session, [e.entity_id for e in page])
    return total, [{**_entity_out(e), "linked_wallets": linked.get(e.entity_id, [])} for e in page]


def get_entity_detail(session: Session, entity_id: str) -> dict[str, Any] | None:
    e = session.get(AddressEntity, entity_id)
    if e is None:
        return None
    addresses = sorted(session.scalars(select(AddressEntityMember.address).where(AddressEntityMember.entity_id == entity_id)))
    ip_links = list(session.scalars(select(EntityIPLink).where(EntityIPLink.entity_id == entity_id)))
    links = list(session.scalars(select(EntityLink).where((EntityLink.entity_a == entity_id) | (EntityLink.entity_b == entity_id))))
    findings = list(session.scalars(select(CorrelationFinding).where(CorrelationFinding.entity_id == entity_id)))
    # Transactions this entity spent FROM (inputs). Transactions where it only received (outputs) are not "its"
    # transactions under this heuristic -- there is no co-spend evidence tying the entity to that spend.
    spending_tx_ids = sorted(set(session.scalars(select(TxInput.transaction_id).where(TxInput.address.in_(addresses)))))
    related_entities = sorted({(link.entity_b if link.entity_a == entity_id else link.entity_a) for link in links})
    return {
        **_entity_out(e),
        "linked_wallets": linked_wallets_for_entities(session, [entity_id]).get(entity_id, []),
        "addresses": addresses,
        "spending_transaction_ids": spending_tx_ids,
        "ip_links": [{"ip_address": l.ip_address, "asn": l.asn, "geo_country": l.geo_country, "transaction_count": l.transaction_count,
                      "first_seen": l.first_seen, "last_seen": l.last_seen} for l in ip_links],
        "related_entities": related_entities,
        "links": [{"entity_a": l.entity_a, "entity_b": l.entity_b, "link_type": l.link_type, "shared_ip_count": l.shared_ip_count, "weight": l.weight} for l in links],
        "findings": [{"finding_type": f.finding_type, "description": f.description, "evidence": f.evidence} for f in findings],
        "note": NOTE,
    }
