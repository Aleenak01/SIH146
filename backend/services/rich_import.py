"""
Enrichment import of rich transaction records (Phase 1).

A rich record adds address-level detail (txid, fee, script type, input/output addresses, a synthetic network flow) to a
transaction. The flat (sender, receiver, amount) transaction that the existing pipeline analyses is never duplicated or changed:

  * the record names a transaction that exists (legacy_transaction_id)  -> the detail rows are attached to it;
  * it names none, or one that does not exist yet                       -> the flat transaction is created through the EXISTING
    ingest function (source 'synthetic', without synthetic network observations, since the flow record is the network layer)
    and the detail rows are attached to that.

Behaviour, matching /api/transactions/batch: every record is checked on its own and reported by index if it is rejected; the valid
ones are written together in ONE database transaction (an unexpected error rolls all of them back). Re-importing changes nothing
(records are de-duplicated by txid); a txid that comes back with different content is rejected, not overwritten.

GeoIP: when a record has src_ip but no geo fields they are filled from the lookup; when it has them they are kept, compared with
the lookup and any difference is reported as a warning. Nothing here reads the analysis results or feeds anything to ml/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import Database
from ..ingestion.base import SourcedTransaction
from ..ingestion.rich_formats import RichTransactionIn, to_sats
from ..models import FlowRecord, Transaction, TxDetails, TxInput, TxOutput
from ..schemas import TransactionIn
from .geoip import GeoIP
from .ingest import WRITE_LOCK, ingest

_CHUNK = 500


@dataclass
class RichImportResult:
    received: int = 0
    attached: int = 0                # detail rows attached to a transaction that already existed
    created: int = 0                 # flat transactions created (through the existing ingest) and detailed
    duplicates: int = 0              # txid already imported with identical content: nothing changed
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    geoip_available: bool = False
    details_total: int = 0           # rows in tx_details after the import

    def as_dict(self) -> dict[str, Any]:
        return {
            "received": self.received, "attached": self.attached, "created": self.created, "duplicates": self.duplicates,
            "rejected": sorted(self.rejected, key=lambda r: r["index"]), "warnings": self.warnings,
            "geoip_available": self.geoip_available, "details_total": self.details_total,
            "note": "Synthetic data: IP addresses are randomly assigned, not observed traffic.",
        }


def _chunks(seq: Sequence, size: int = _CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i: i + size]


def _signature_from_record(r: RichTransactionIn) -> tuple:
    return (round(r.fee_btc, 8), r.script_type,
            tuple(zip(r.input_addresses, map(to_sats, r.input_amounts))), tuple(zip(r.output_addresses, map(to_sats, r.output_amounts))))


def _signature_from_db(d: TxDetails, inputs: list[TxInput], outputs: list[TxOutput]) -> tuple:
    return (round(d.fee_btc, 8), d.script_type,
            tuple((i.address, to_sats(i.amount_btc)) for i in sorted(inputs, key=lambda x: x.position)),
            tuple((o.address, to_sats(o.amount_btc)) for o in sorted(outputs, key=lambda x: x.position)))


def import_rich(db: Database, records: Sequence[tuple[int, RichTransactionIn]], geo: GeoIP, *, received: int | None = None,
                rejected: Sequence[dict[str, Any]] = ()) -> RichImportResult:
    """
    records: [(index, validated record)]; `rejected`: records that already failed parsing/validation (kept in the report);
    `received`: total records in the request (defaults to valid + rejected).
    """
    result = RichImportResult(received=received if received is not None else len(records) + len(rejected), rejected=list(rejected),
                              geoip_available=geo.available)
    with WRITE_LOCK, db.transaction() as s:
        _import(s, records, geo, result)
        result.details_total = s.scalar(select(func.count()).select_from(TxDetails)) or 0
    return result


def _import(s: Session, records: Sequence[tuple[int, RichTransactionIn]], geo: GeoIP, result: RichImportResult) -> None:
    # ---- what exists already ------------------------------------------------------------------------------------------------
    txids = [r.txid for _, r in records]
    existing: dict[str, TxDetails] = {}
    for part in _chunks(txids):
        for d in s.scalars(select(TxDetails).where(TxDetails.txid.in_(part))):
            existing[d.txid] = d
    legacy_ids = sorted({r.legacy_transaction_id for _, r in records if r.legacy_transaction_id})
    transactions: dict[str, Transaction] = {}
    for part in _chunks(legacy_ids):
        for t in s.scalars(select(Transaction).where(Transaction.transaction_id.in_(part))):
            transactions[t.transaction_id] = t
    detailed_ids: dict[str, str] = {}                                   # transaction_id -> txid, for transactions that already have details
    for part in _chunks(sorted(transactions)):
        for tid, txid in s.execute(select(TxDetails.transaction_id, TxDetails.txid).where(TxDetails.transaction_id.in_(part))):
            detailed_ids[tid] = txid

    seen: dict[str, tuple] = {}                                         # txid -> signature, within this request

    for index, r in records:
        sig = _signature_from_record(r)

        # ---- idempotency by txid ---------------------------------------------------------------------------------------------
        if r.txid in seen:
            if seen[r.txid] == sig:
                result.duplicates += 1
            else:
                result.rejected.append({"index": index, "error": f"txid {r.txid} appears twice in this request with different content"})
            continue
        if r.txid in existing:
            d = existing[r.txid]
            inputs = list(s.scalars(select(TxInput).where(TxInput.transaction_id == d.transaction_id)))
            outputs = list(s.scalars(select(TxOutput).where(TxOutput.transaction_id == d.transaction_id)))
            if _signature_from_db(d, inputs, outputs) == sig:
                result.duplicates += 1
            else:
                result.rejected.append({"index": index, "error": f"txid {r.txid} was already imported with different content; it is not overwritten"})
            seen[r.txid] = sig
            continue

        # ---- find or create the flat transaction --------------------------------------------------------------------------------
        tx = transactions.get(r.legacy_transaction_id) if r.legacy_transaction_id else None
        created = False
        if tx is not None:
            problem = _flat_mismatch(tx, r)
            if problem:
                result.rejected.append({"index": index, "error": problem})
                continue
            if tx.transaction_id in detailed_ids:
                result.rejected.append({"index": index, "error": f"transaction {tx.transaction_id} already has details (txid {detailed_ids[tx.transaction_id]})"})
                continue
            if (tx.input_count, tx.output_count) != (len(r.input_addresses), len(r.output_addresses)):
                result.warnings.append({"index": index, "warning": f"stored transaction has {tx.input_count} inputs / {tx.output_count} outputs; "
                                                                   f"the record has {len(r.input_addresses)} / {len(r.output_addresses)} (stored counts kept)"})
        else:
            if not (r.sender_wallet and r.receiver_wallet and r.amount_btc):
                result.rejected.append({"index": index, "error": "the flat transaction does not exist yet, so sender_wallet, receiver_wallet and amount_btc are required to create it"})
                continue
            try:
                flat = TransactionIn(transaction_id=r.legacy_transaction_id, timestamp=r.timestamp, sender_wallet=r.sender_wallet,
                                     receiver_wallet=r.receiver_wallet, amount_btc=r.amount_btc,
                                     input_count=len(r.input_addresses), output_count=len(r.output_addresses))
            except ValueError as e:
                result.rejected.append({"index": index, "error": f"cannot create the flat transaction: {e}"[:300]})
                continue
            outcome = ingest(s, [SourcedTransaction(flat, source_ref=f"rich:{r.txid}")], "synthetic", observe=False)
            if not outcome.transaction_ids:
                reason = outcome.rejected[0]["error"] if outcome.rejected else "it already exists"
                result.rejected.append({"index": index, "error": f"cannot create the flat transaction: {reason}"[:300]})
                continue
            tx = s.get(Transaction, outcome.transaction_ids[0])
            transactions[tx.transaction_id] = tx
            created = True

        # ---- attach the detail rows -----------------------------------------------------------------------------------------------
        s.add(TxDetails(transaction_id=tx.transaction_id, txid=r.txid, fee_btc=round(r.fee_btc, 8), script_type=r.script_type, source=tx.source))
        for pos, (addr, amt) in enumerate(zip(r.input_addresses, r.input_amounts)):
            s.add(TxInput(transaction_id=tx.transaction_id, position=pos, address=addr, amount_btc=round(amt, 8)))
        for pos, (addr, amt) in enumerate(zip(r.output_addresses, r.output_amounts)):
            s.add(TxOutput(transaction_id=tx.transaction_id, position=pos, address=addr, amount_btc=round(amt, 8)))
        if r.has_flow:
            s.add(_flow(tx.transaction_id, r, geo, index, result))
        detailed_ids[tx.transaction_id] = r.txid
        seen[r.txid] = sig
        if created:
            result.created += 1
        else:
            result.attached += 1
    s.flush()


def _flat_mismatch(tx: Transaction, r: RichTransactionIn) -> str | None:
    """The record must describe the stored transaction, not a different one that happens to share the id."""
    if r.sender_wallet and r.sender_wallet != tx.sender_wallet:
        return f"legacy_transaction_id {tx.transaction_id} has sender {tx.sender_wallet}, the record says {r.sender_wallet}"
    if r.receiver_wallet and r.receiver_wallet != tx.receiver_wallet:
        return f"legacy_transaction_id {tx.transaction_id} has receiver {tx.receiver_wallet}, the record says {r.receiver_wallet}"
    if r.amount_btc is not None and to_sats(r.amount_btc) != to_sats(tx.amount_btc):
        return f"legacy_transaction_id {tx.transaction_id} has amount {tx.amount_btc:g} BTC, the record says {r.amount_btc:g}"
    if r.timestamp != tx.timestamp:
        return f"legacy_transaction_id {tx.transaction_id} has timestamp {tx.timestamp.isoformat()}, the record says {r.timestamp.isoformat()}"
    if tx.source != "synthetic":
        return f"transaction {tx.transaction_id} belongs to a different data source ({tx.source})"
    return None


def _flow(transaction_id: str, r: RichTransactionIn, geo: GeoIP, index: int, result: RichImportResult) -> FlowRecord:
    country, asn, org = r.geo_country, r.asn, r.asn_org
    if r.src_ip:
        info = geo.lookup(r.src_ip)
        for name, given, looked_up in (("geo_country", country, info["country_code"]), ("asn", asn, info["asn"]), ("asn_org", org, info["asn_org"])):
            if given is not None and looked_up is not None and given != looked_up:
                result.warnings.append({"index": index, "warning": f"{name} {given!r} differs from the GeoIP lookup of {r.src_ip} ({looked_up!r}); the given value is kept"})
        country = country if country is not None else info["country_code"]
        asn = asn if asn is not None else info["asn"]
        org = org if org is not None else info["asn_org"]
    return FlowRecord(transaction_id=transaction_id, src_ip=r.src_ip, dst_ip=r.dst_ip, src_port=r.src_port, dst_port=r.dst_port,
                      geo_country=country, asn=asn, asn_org=org, is_synthetic=True, origin="synthetic_flow_record")
