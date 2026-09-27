"""
Correlation between the network layer (flow_records: synthetic IP/ASN/country/ports) and the blockchain layer
(address entities from common-input-ownership clustering), built via the shared txid -- built ONLY from the rich
address-level tables (see entities.py for the same rule). Never reads sender_wallet, receiver_wallet, amount_btc,
wallets, or the ground-truth file.

Links
  entity <-> IP     an entity spent from an address whose transaction's synthetic flow record had this src_ip
                     (transaction_count, first/last seen).
  entity <-> entity  two entities whose spending transactions were seen from the SAME synthetic src_ip ("shared_ip";
                     weight = number of distinct shared IPs).

Findings (evidence, never verdicts -- each keeps the underlying evidence so an investigator can check it themselves)
  ip_used_by_several_entities   one synthetic IP used by 2+ different entities
  entity_many_countries         one entity's spending transactions were seen from several different countries
  entity_many_asns              one entity's spending transactions were seen from several different networks (ASNs)
  entity_unusual_port           an entity sent to a destination port other than the common Bitcoin port (8333)

Everything here is built on SYNTHETIC data. A shared synthetic IP indicates a possible link, never proof: by
Phase 1's generator design, about 15% of wallets share an IP with another wallet for demonstration purposes, with no
real-world meaning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select

from ..database import Database
from ..models import AddressEntity, AddressEntityMember, CorrelationFinding, EntityIPLink, EntityLink, FlowRecord, Transaction, TxInput
from ..services.ingest import WRITE_LOCK

COMMON_DST_PORT = 8333
MANY_COUNTRIES_THRESHOLD = 3
MANY_ASNS_THRESHOLD = 3

FINDING_LABELS = {
    "ip_used_by_several_entities": "A synthetic IP address is shared by more than one entity.",
    "entity_many_countries": "This entity's transactions were seen from several different (synthetic) countries.",
    "entity_many_asns": "This entity's transactions were seen from several different (synthetic) networks (ASNs).",
    "entity_unusual_port": "This entity sent to a destination port other than the common Bitcoin port (8333).",
}
SYNTHETIC_NOTE = "Synthetic data: this is a possible-link indicator from generated demo network data, not proof of anything."


@dataclass
class CorrelationRefresh:
    entity_ip_links: int = 0
    entity_links: int = 0
    findings: int = 0
    rich_data_available: bool = True


def refresh_correlation(db: Database, source: str = "synthetic") -> CorrelationRefresh:
    with db.session() as s:
        entity_ids = list(s.scalars(select(AddressEntity.entity_id).where(AddressEntity.source == source)))
        if not entity_ids:
            return CorrelationRefresh(rich_data_available=False)
        members = list(s.execute(select(AddressEntityMember.entity_id, AddressEntityMember.address).where(AddressEntityMember.entity_id.in_(entity_ids))))
        addr_to_entity = dict((addr, eid) for eid, addr in members)
        input_rows = list(s.execute(select(TxInput.transaction_id, TxInput.address).where(TxInput.address.in_(addr_to_entity))))
        entity_tx: dict[str, set[str]] = defaultdict(set)
        for tid, addr in input_rows:
            eid = addr_to_entity.get(addr)
            if eid:
                entity_tx[eid].add(tid)
        all_tx_ids = {tid for tids in entity_tx.values() for tid in tids}
        flows = {f.transaction_id: f for f in s.scalars(select(FlowRecord).where(FlowRecord.transaction_id.in_(all_tx_ids)))}
        tx_times = dict(s.execute(select(Transaction.transaction_id, Transaction.timestamp).where(Transaction.transaction_id.in_(all_tx_ids))).all())

    # ---- entity <-> IP -----------------------------------------------------------------------------------------
    ip_links: dict[tuple[str, str], dict] = {}
    ip_to_entities: dict[str, set[str]] = defaultdict(set)
    entity_ports: dict[str, set[int]] = defaultdict(set)
    entity_countries: dict[str, set[str]] = defaultdict(set)
    entity_asns: dict[str, set[int]] = defaultdict(set)
    for eid, tids in entity_tx.items():
        for tid in tids:
            f = flows.get(tid)
            if f is None or not f.src_ip:
                continue
            t = tx_times.get(tid)
            key = (eid, f.src_ip)
            link = ip_links.setdefault(key, {"transaction_count": 0, "first_seen": None, "last_seen": None, "asn": f.asn, "geo_country": f.geo_country})
            link["transaction_count"] += 1
            if t is not None:
                link["first_seen"] = t if link["first_seen"] is None else min(link["first_seen"], t)
                link["last_seen"] = t if link["last_seen"] is None else max(link["last_seen"], t)
            ip_to_entities[f.src_ip].add(eid)
            if f.dst_port:
                entity_ports[eid].add(f.dst_port)
            if f.geo_country:
                entity_countries[eid].add(f.geo_country)
            if f.asn:
                entity_asns[eid].add(f.asn)

    # ---- entity <-> entity via a shared IP ---------------------------------------------------------------------
    entity_link_counts: dict[tuple[str, str], int] = defaultdict(int)
    for eids in ip_to_entities.values():
        ordered = sorted(eids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                entity_link_counts[(a, b)] += 1

    # ---- findings ------------------------------------------------------------------------------------------------
    findings: list[dict] = []
    for ip, eids in ip_to_entities.items():
        if len(eids) >= 2:
            findings.append({"entity_id": min(eids), "finding_type": "ip_used_by_several_entities",
                             "description": f"IP {ip} is used by {len(eids)} entities: {', '.join(sorted(eids))}.",
                             "evidence": {"ip": ip, "entities": sorted(eids)}})
    for eid, countries in entity_countries.items():
        if len(countries) >= MANY_COUNTRIES_THRESHOLD:
            findings.append({"entity_id": eid, "finding_type": "entity_many_countries",
                             "description": f"Entity {eid} was seen from {len(countries)} different countries: {', '.join(sorted(countries))}.",
                             "evidence": {"countries": sorted(countries)}})
    for eid, asns in entity_asns.items():
        if len(asns) >= MANY_ASNS_THRESHOLD:
            findings.append({"entity_id": eid, "finding_type": "entity_many_asns",
                             "description": f"Entity {eid} was seen from {len(asns)} different networks (ASNs): {', '.join(map(str, sorted(asns)))}.",
                             "evidence": {"asns": sorted(asns)}})
    for eid, ports in entity_ports.items():
        unusual = sorted(p for p in ports if p != COMMON_DST_PORT)
        if unusual:
            findings.append({"entity_id": eid, "finding_type": "entity_unusual_port",
                             "description": f"Entity {eid} sent to non-standard destination port(s): {', '.join(map(str, unusual))}.",
                             "evidence": {"ports": unusual}})

    result = CorrelationRefresh()
    with WRITE_LOCK, db.transaction() as s:
        s.execute(delete(EntityIPLink).where(EntityIPLink.entity_id.in_(entity_ids)))
        for (eid, ip), data in ip_links.items():
            s.add(EntityIPLink(entity_id=eid, ip_address=ip, asn=data["asn"], geo_country=data["geo_country"],
                               transaction_count=data["transaction_count"], first_seen=data["first_seen"], last_seen=data["last_seen"]))
            result.entity_ip_links += 1
        s.execute(delete(EntityLink).where(EntityLink.entity_a.in_(entity_ids)))
        for (a, b), n in entity_link_counts.items():
            s.add(EntityLink(entity_a=a, entity_b=b, link_type="shared_ip", shared_ip_count=n, weight=n))
            result.entity_links += 1
        s.execute(delete(CorrelationFinding).where(CorrelationFinding.source == source))
        for f in findings:
            s.add(CorrelationFinding(entity_id=f["entity_id"], finding_type=f["finding_type"], description=f["description"], evidence=f["evidence"], source=source))
            result.findings += 1
    return result
