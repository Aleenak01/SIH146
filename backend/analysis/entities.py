"""
Common-input-ownership entity clustering (Phase 2).

Heuristic: addresses spent together as inputs of the same transaction are very likely controlled by the same
person or wallet software (the classic "common-input-ownership" heuristic used throughout Bitcoin forensics).
Addresses that never co-spend with another address stay their own singleton entity.

Uses ONLY the rich address-level tables (tx_inputs, tx_outputs, tx_details, flow_records). It must NOT and does NOT
read sender_wallet, receiver_wallet, amount_btc, wallets, or the address-ownership file kept in dataset/rich/ for
offline validation only (see scripts/validate_entities.py). A test (test_entities.py) inspects this module's source
and fails if any of those are referenced.

Scope: only addresses that appear at least once as a transaction INPUT (i.e. an address that was spent from) become
part of an entity. An address that is only ever an output (received into, never spent from, within this dataset)
has no co-spend evidence and is not given an entity of its own by this heuristic; it still counts toward the
entities it eventually joins once spent from, and toward received-BTC totals of whichever entity spends it later.

An entity is a heuristic grouping: "likely common control", never proof. Common-input-ownership is well known to be
broken by CoinJoin-like transactions (independent people co-signing one transaction to look like one payer).

Phase 3: transactions flagged by analysis/coinjoin.py (CoinJoinCandidate) are excluded from the union-find input set
below, so a CoinJoin-like transaction's co-spend is not treated as evidence that its participants are one entity. If
CoinJoin detection has never been run for this source (the table is empty), nothing is excluded and behaviour is
identical to before this fix -- CoinJoin exclusion is additive, not a prerequisite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from sqlalchemy import delete, select

from ..database import Database
from ..models import AddressEntity, AddressEntityMember, CoinJoinCandidate, FlowRecord, Transaction, TxDetails, TxInput, TxOutput
from ..services.ingest import WRITE_LOCK

METHOD = "common_input_ownership"
METHOD_LABEL = "Common-input-ownership (addresses spent together in one transaction)"
ID_PREFIX = "CIO"
NOTE = ("Entity: addresses this heuristic believes are controlled by the same wallet or person, because they were "
        "spent together as inputs of one transaction. This indicates likely common control, not proof, and is not "
        "evidence of wrongdoing. CoinJoin-like transactions can break this heuristic. Requires investigator review.")


# ---- pure computation (framework-independent; same shape as analysis/clustering.py's _UnionFind) ------------------
class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


@dataclass
class EntityData:
    addresses: list[str]
    transaction_ids: set[str] = field(default_factory=set)   # transactions where an address of this entity is an INPUT
    _assigned_id: str | None = None

    @property
    def default_id(self) -> str:
        """Id for a brand-new entity: prefix + its lowest address."""
        return f"{ID_PREFIX}-{min(self.addresses)}"

    @property
    def entity_id(self) -> str:
        return self._assigned_id or self.default_id


def compute_common_input_entities(inputs: Iterable[tuple[str, str]]) -> list[EntityData]:
    """
    inputs: (transaction_id, address) for every tx_inputs row. Addresses spent together in one transaction join one
    entity (union-find). Deterministic: the same input set always produces the same groups.
    """
    uf = _UnionFind()
    by_tx: dict[str, list[str]] = {}
    for tid, addr in inputs:
        by_tx.setdefault(tid, []).append(addr)
        uf.find(addr)
    for addrs in by_tx.values():
        for a in addrs[1:]:
            uf.union(addrs[0], a)

    groups: dict[str, EntityData] = {}
    for tid, addrs in by_tx.items():
        root = uf.find(addrs[0])
        g = groups.setdefault(root, EntityData([]))
        for a in addrs:
            if a not in g.addresses:
                g.addresses.append(a)
        g.transaction_ids.add(tid)
    for g in groups.values():
        g.addresses.sort()
    return sorted(groups.values(), key=lambda g: g.entity_id)


def assign_stable_ids(computed: list[EntityData], existing: dict[str, set[str]]) -> None:
    """
    Keep entity ids stable as entities grow or merge. Union-find over an ever-growing set of transactions is
    monotonic (entities only merge, never split), but the *default* id (lowest address) can still change when two
    existing entities merge, so ids are matched by largest address overlap first -- the same approach as the wallet
    clusters in analysis/clustering.py. `existing`: entity_id -> its current address set.
    """
    pairs = []
    for i, g in enumerate(computed):
        addrs = set(g.addresses)
        for eid, members in existing.items():
            if overlap := len(addrs & members):
                pairs.append((-overlap, g.default_id != eid, eid, i))
    taken: set[str] = set()
    assigned: dict[int, str] = {}
    for _neg, _tie, eid, i in sorted(pairs):
        if i not in assigned and eid not in taken:
            assigned[i] = eid
            taken.add(eid)
    reserved = set(existing) | taken
    for i, g in enumerate(computed):
        if i in assigned:
            g._assigned_id = assigned[i]
            continue
        new_id, n = g.default_id, 1
        while new_id in reserved:
            n += 1
            new_id = f"{g.default_id}-{n}"
        reserved.add(new_id)
        g._assigned_id = new_id


# ---- persistence ----------------------------------------------------------------------------------------------
@dataclass
class EntityRefresh:
    entities: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    addresses_total: int = 0
    rich_data_available: bool = True


def refresh_entities(db: Database, source: str = "synthetic") -> EntityRefresh:
    """Recompute common-input-ownership entities for one source's rich (address-level) data and upsert them."""
    with db.session() as s:
        tx_ids = set(s.scalars(select(TxDetails.transaction_id).where(TxDetails.source == source)))
        if not tx_ids:
            return EntityRefresh(rich_data_available=False)
        coinjoin_ids = set(s.scalars(select(CoinJoinCandidate.transaction_id).where(CoinJoinCandidate.source == source)))
        input_tx_ids = tx_ids - coinjoin_ids   # Phase 3: a CoinJoin-like transaction's co-spend is not evidence of common ownership
        inputs = [tuple(r) for r in s.execute(select(TxInput.transaction_id, TxInput.address).where(TxInput.transaction_id.in_(input_tx_ids)))]
    if not inputs:
        return EntityRefresh(rich_data_available=False)
    computed = compute_common_input_entities(inputs)

    with db.session() as s:
        existing_members: dict[str, set[str]] = {}
        for eid, addr in s.execute(select(AddressEntityMember.entity_id, AddressEntityMember.address)):
            existing_members.setdefault(eid, set()).add(addr)
        existing_ids = set(s.scalars(select(AddressEntity.entity_id).where(AddressEntity.source == source)))
    existing = {eid: m for eid, m in existing_members.items() if eid in existing_ids}
    assign_stable_ids(computed, existing)

    # ---- per-entity aggregates -------------------------------------------------------------------------------------
    all_tx_ids = sorted({tid for g in computed for tid in g.transaction_ids})
    with db.session() as s:
        tx_times = dict(s.execute(select(Transaction.transaction_id, Transaction.timestamp).where(Transaction.transaction_id.in_(all_tx_ids))).all())
        in_rows = list(s.execute(select(TxInput.transaction_id, TxInput.address, TxInput.amount_btc).where(TxInput.transaction_id.in_(all_tx_ids))))
        out_rows = list(s.execute(select(TxOutput.transaction_id, TxOutput.address, TxOutput.amount_btc).where(TxOutput.transaction_id.in_(all_tx_ids))))
        flows = {f.transaction_id: f for f in s.scalars(select(FlowRecord).where(FlowRecord.transaction_id.in_(all_tx_ids)))}

    in_by_addr: dict[str, list[tuple[str, float]]] = {}
    for tid, addr, amt in in_rows:
        in_by_addr.setdefault(addr, []).append((tid, amt))
    out_by_addr: dict[str, list[tuple[str, float]]] = {}
    for tid, addr, amt in out_rows:
        out_by_addr.setdefault(addr, []).append((tid, amt))

    result = EntityRefresh(entities=len(computed))
    with WRITE_LOCK, db.transaction() as s:
        existing_rows = {e.entity_id: e for e in s.scalars(select(AddressEntity).where(AddressEntity.source == source))}
        keep: set[str] = set()
        for g in computed:
            eid = g.entity_id
            keep.add(eid)
            addr_set = set(g.addresses)
            sent = sum(amt for a in g.addresses for _, amt in in_by_addr.get(a, []))
            received = sum(amt for a in g.addresses for _, amt in out_by_addr.get(a, []))
            times: list[datetime] = [tx_times[tid] for tid in g.transaction_ids if tid in tx_times]
            touching_tx = set(g.transaction_ids)
            for a in g.addresses:
                touching_tx.update(tid for tid, _ in out_by_addr.get(a, []))
            countries, asns, ips = set(), set(), set()
            for tid in g.transaction_ids:      # only spending (input-side) flows count as "this entity's" network activity
                f = flows.get(tid)
                if f:
                    if f.geo_country:
                        countries.add(f.geo_country)
                    if f.asn:
                        asns.add(f.asn)
                    if f.src_ip:
                        ips.add(f.src_ip)
            values = dict(
                address_count=len(addr_set), transaction_count=len(touching_tx),
                first_seen=min(times) if times else None, last_seen=max(times) if times else None,
                total_sent_btc=round(sent, 8), total_received_btc=round(received, 8),
                distinct_ip_count=len(ips), distinct_asn_count=len(asns), distinct_country_count=len(countries),
                countries=sorted(countries), asns=sorted(asns),
            )
            row = existing_rows.get(eid)
            if row is None:
                s.add(AddressEntity(entity_id=eid, method=METHOD, source=source, **values))
                result.created += 1
                changed_members = True
            else:
                changed = False
                for k, v in values.items():
                    if getattr(row, k) != v:
                        setattr(row, k, v)
                        changed = True
                result.updated += changed
                current = set(s.scalars(select(AddressEntityMember.address).where(AddressEntityMember.entity_id == eid)))
                changed_members = current != addr_set
            if changed_members:
                s.execute(delete(AddressEntityMember).where(AddressEntityMember.entity_id == eid))
                s.flush()
                s.add_all([AddressEntityMember(entity_id=eid, address=a) for a in g.addresses])
        for eid in set(existing_rows) - keep:
            s.execute(delete(AddressEntityMember).where(AddressEntityMember.entity_id == eid))
            s.execute(delete(AddressEntity).where(AddressEntity.entity_id == eid))
            result.removed += 1
        result.addresses_total = sum(len(g.addresses) for g in computed)
    return result
