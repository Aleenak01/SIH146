"""
Entity relationship graph (Phase 2): entities, addresses, transactions, and the SYNTHETIC network (IP/ASN/country)
that connects them -- built entirely from the rich address-level tables. This is a separate graph from
services/graph.py's wallet-level `/api/graph`; neither reads the other.

Node types   entity - address - transaction - ip - asn - country
Edge types   in_entity (address -> entity) - input_of (address -> transaction) - output_to (transaction -> address) -
             sent_from_ip (transaction -> ip) - in_asn (ip -> asn) - in_country (ip -> country) -
             shared_ip (entity <-> entity)

All SYNTHETIC. A shared_ip edge indicates a possible link, never proof of common ownership.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import AddressEntity, AddressEntityMember, EntityIPLink, EntityLink, FlowRecord, TxInput, TxOutput

NODE_TYPES = ("entity", "address", "transaction", "ip", "asn", "country")
EDGE_TYPES = ("in_entity", "input_of", "output_to", "sent_from_ip", "in_asn", "in_country", "shared_ip")
HARD_MAX_NODES = 500
SYNTHETIC_NOTE = "Synthetic data: IPs, ASNs and countries are generated demo values, not observed traffic."


@dataclass
class EntityGraphRequest:
    focus_type: str                    # entity | address | ip | txid
    focus_id: str
    depth: int = 1
    node_types: set[str] = field(default_factory=lambda: {"entity", "address", "transaction", "ip"})
    edge_types: set[str] | None = None
    max_nodes: int = 150


def _resolve_focus(session: Session, req: EntityGraphRequest) -> list[str]:
    if req.focus_type == "entity":
        if session.get(AddressEntity, req.focus_id) is None:
            raise AppError(404, "not_found", f"No entity {req.focus_id!r}.")
        return [req.focus_id]
    if req.focus_type == "address":
        ids = list(session.scalars(select(AddressEntityMember.entity_id).where(AddressEntityMember.address == req.focus_id)))
        if not ids:
            raise AppError(404, "not_found", f"Address {req.focus_id!r} is not part of any entity.")
        return ids
    if req.focus_type in ("txid", "transaction"):
        addrs = set(session.scalars(select(TxInput.address).where(TxInput.transaction_id == req.focus_id))) | \
                set(session.scalars(select(TxOutput.address).where(TxOutput.transaction_id == req.focus_id)))
        if not addrs:
            raise AppError(404, "not_found", f"No transaction {req.focus_id!r} with address-level detail.")
        ids = sorted(set(session.scalars(select(AddressEntityMember.entity_id).where(AddressEntityMember.address.in_(addrs)))))
        if not ids:
            raise AppError(404, "not_found", f"Transaction {req.focus_id!r} has no addresses that belong to an entity.")
        return ids
    if req.focus_type == "ip":
        ids = sorted(set(session.scalars(select(EntityIPLink.entity_id).where(EntityIPLink.ip_address == req.focus_id))))
        if not ids:
            raise AppError(404, "not_found", f"IP {req.focus_id!r} has no entity links.")
        return ids
    raise AppError(422, "validation_error", f"Unknown focus {req.focus_type!r}.")


def build_entity_graph(session: Session, req: EntityGraphRequest) -> dict[str, Any]:
    node_types = set(req.node_types) | {"entity"}
    max_nodes = min(req.max_nodes, HARD_MAX_NODES)
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    truncated = False
    omitted: Counter = Counter()

    def add_node(nid: str, ntype: str, label: str, data: dict | None = None) -> bool:
        nonlocal truncated
        if nid in nodes:
            return True
        if len(nodes) >= max_nodes:
            truncated = True
            omitted[ntype] += 1
            return False
        nodes[nid] = {"id": nid, "type": ntype, "label": label, "data": data or {}}
        return True

    def add_edge(etype: str, source: str, target: str, **data: Any) -> None:
        eid = f"{etype}:{source}->{target}"
        if eid not in edges and source in nodes and target in nodes:
            edges[eid] = {"id": eid, "source": source, "target": target, "type": etype, "data": data}

    focus_ids = _resolve_focus(session, req)

    # ---- expand by shared_ip links up to `depth` -----------------------------------------------------------------
    known = set(focus_ids)
    frontier = set(focus_ids)
    for _ in range(max(0, req.depth)):
        if not frontier or len(known) >= max_nodes:
            break
        links = list(session.scalars(select(EntityLink).where(EntityLink.entity_a.in_(frontier) | EntityLink.entity_b.in_(frontier))))
        added = set()
        for link in links:
            for a, b in ((link.entity_a, link.entity_b), (link.entity_b, link.entity_a)):
                if a in frontier and b not in known:
                    added.add(b)
        known |= added
        frontier = added

    entities = list(session.scalars(select(AddressEntity).where(AddressEntity.entity_id.in_(known))))
    for e in entities:
        add_node(f"entity:{e.entity_id}", "entity", e.entity_id, {
            "address_count": e.address_count, "transaction_count": e.transaction_count,
            "total_sent_btc": e.total_sent_btc, "total_received_btc": e.total_received_btc,
            "is_focus": e.entity_id in focus_ids})

    entity_addrs: dict[str, list[str]] = {}
    if "address" in node_types:
        rows = list(session.execute(select(AddressEntityMember.entity_id, AddressEntityMember.address).where(AddressEntityMember.entity_id.in_(known))))
        for eid, addr in rows:
            entity_addrs.setdefault(eid, []).append(addr)
        for eid, addrs in entity_addrs.items():
            for addr in addrs:
                if add_node(f"address:{addr}", "address", addr):
                    add_edge("in_entity", f"address:{addr}", f"entity:{eid}")

    tx_ids: set[str] = set()
    if "transaction" in node_types and entity_addrs:
        all_addrs = {a for addrs in entity_addrs.values() for a in addrs}
        in_rows = list(session.execute(select(TxInput.transaction_id, TxInput.address, TxInput.amount_btc).where(TxInput.address.in_(all_addrs))))
        out_rows = list(session.execute(select(TxOutput.transaction_id, TxOutput.address, TxOutput.amount_btc).where(TxOutput.address.in_(all_addrs))))
        tx_ids = {tid for tid, _, _ in in_rows} | {tid for tid, _, _ in out_rows}
        for tid in tx_ids:
            add_node(f"tx:{tid}", "transaction", tid)
        for tid, addr, amt in in_rows:
            add_edge("input_of", f"address:{addr}", f"tx:{tid}", amount_btc=amt)
        for tid, addr, amt in out_rows:
            add_edge("output_to", f"tx:{tid}", f"address:{addr}", amount_btc=amt)

    if tx_ids and (node_types & {"ip", "asn", "country"}):
        flows = list(session.scalars(select(FlowRecord).where(FlowRecord.transaction_id.in_(tx_ids))))
        for f in flows:
            if not f.src_ip:
                continue
            if "ip" in node_types and add_node(f"ip:{f.src_ip}", "ip", f.src_ip, {"synthetic": True}):
                add_edge("sent_from_ip", f"tx:{f.transaction_id}", f"ip:{f.src_ip}")
            elif "ip" in node_types:
                add_edge("sent_from_ip", f"tx:{f.transaction_id}", f"ip:{f.src_ip}")
            if "asn" in node_types and f.asn and "ip" in node_types:
                add_node(f"asn:{f.asn}", "asn", str(f.asn), {"asn_org": f.asn_org})
                add_edge("in_asn", f"ip:{f.src_ip}", f"asn:{f.asn}")
            if "country" in node_types and f.geo_country and "ip" in node_types:
                add_node(f"country:{f.geo_country}", "country", f.geo_country)
                add_edge("in_country", f"ip:{f.src_ip}", f"country:{f.geo_country}")

    links = list(session.scalars(select(EntityLink).where(EntityLink.entity_a.in_(known) & EntityLink.entity_b.in_(known))))
    for link in links:
        add_edge("shared_ip", f"entity:{link.entity_a}", f"entity:{link.entity_b}", shared_ip_count=link.shared_ip_count, weight=link.weight)

    if req.edge_types is not None:
        edges = {k: v for k, v in edges.items() if v["type"] in req.edge_types}

    node_list = list(nodes.values())
    return {
        "focus": {"type": req.focus_type, "id": req.focus_id},
        "nodes": node_list, "edges": list(edges.values()),
        "truncated": truncated, "omitted": dict(omitted),
        "counts": {"nodes": dict(Counter(n["type"] for n in node_list)), "edges": dict(Counter(e["type"] for e in edges.values()))},
        "notes": [SYNTHETIC_NOTE, "An entity link (shared_ip) indicates a possible connection, never proof of common ownership or wrongdoing."],
    }
