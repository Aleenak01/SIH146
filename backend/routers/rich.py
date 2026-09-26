"""Rich transaction records (Phase 1): enrichment import, per-transaction details, GeoIP status. Everything here is SYNTHETIC data."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import Database
from ..deps import get_db, get_session, get_settings
from ..errors import AppError
from ..ingestion.rich_formats import FORMATS, MAX_BYTES, FormatError, parse, validate_records
from ..models import FlowRecord, Transaction, TxDetails, TxInput, TxOutput
from ..services.geoip import GeoIP
from ..services.rich_import import import_rich

router = APIRouter(tags=["rich transactions (synthetic)"])

SYNTHETIC_NOTE = "Synthetic data: IP addresses and ports are randomly assigned values, not observed traffic."


def get_geoip(request: Request) -> GeoIP:
    """One shared GeoIP per app (opened lazily). Tests may set app.state.geoip to a stand-in."""
    geo = getattr(request.app.state, "geoip", None)
    if geo is None:
        geo = request.app.state.geoip = GeoIP.from_settings(request.app.state.settings)
    return geo


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise AppError(413, "payload_too_large", f"The request body is larger than the {MAX_BYTES // (1024 * 1024)} MB limit.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise AppError(413, "payload_too_large", f"The request body is larger than the {MAX_BYTES // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/import/rich")
async def import_rich_records(
    request: Request,
    fmt: Annotated[str, Query(alias="format", description="csv, json, jsonl or xml")],
    db: Database = Depends(get_db),
    geo: GeoIP = Depends(get_geoip),
) -> dict[str, Any]:
    """
    Enrichment import of rich transaction records (raw request body in the given format).

    A record that names an existing transaction (legacy_transaction_id) gets its address-level detail attached; otherwise the flat
    transaction is created through the normal ingest first. Records are checked one by one: rejected ones are listed with their
    index and reason, the valid ones are saved together. Re-importing changes nothing (de-duplicated by txid).
    """
    if fmt not in FORMATS:
        raise AppError(422, "invalid_format", f"format must be one of {', '.join(FORMATS)}")
    body = await _read_body(request)

    def work() -> dict[str, Any]:
        try:
            raw, parse_rejected = parse(body, fmt)
        except FormatError as e:
            raise AppError(422, "invalid_format", str(e)) from e
        valid, invalid = validate_records(raw)
        result = import_rich(db, valid, geo, received=len(raw) + len(parse_rejected), rejected=list(parse_rejected) + invalid)
        return {"format": fmt, **result.as_dict()}

    return await run_in_threadpool(work)


class RichInputOut(BaseModel):
    position: int
    address: str
    amount_btc: float


class FlowOut(BaseModel):
    src_ip: str | None
    dst_ip: str | None
    src_port: int | None
    dst_port: int | None
    geo_country: str | None
    asn: int | None
    asn_org: str | None
    is_synthetic: bool
    origin: str


class TxDetailsOut(BaseModel):
    transaction_id: str
    txid: str
    fee_btc: float
    script_type: str
    source: str
    inputs: list[RichInputOut]
    outputs: list[RichInputOut]
    flow: FlowOut | None
    note: str = SYNTHETIC_NOTE


@router.get("/api/transactions/{transaction_id}/details", response_model=TxDetailsOut)
def transaction_details(transaction_id: str, session: Session = Depends(get_session)):
    """Address-level detail of one transaction (txid, fee, script type, inputs, outputs, synthetic network flow)."""
    if session.get(Transaction, transaction_id) is None:
        raise AppError(404, "not_found", f"No transaction {transaction_id!r}.")
    d = session.scalars(select(TxDetails).where(TxDetails.transaction_id == transaction_id)).first()
    if d is None:
        raise AppError(404, "no_details", f"Transaction {transaction_id!r} has no address-level details (import rich records to add them).")
    inputs = session.scalars(select(TxInput).where(TxInput.transaction_id == transaction_id).order_by(TxInput.position))
    outputs = session.scalars(select(TxOutput).where(TxOutput.transaction_id == transaction_id).order_by(TxOutput.position))
    flow = session.scalars(select(FlowRecord).where(FlowRecord.transaction_id == transaction_id)).first()
    return TxDetailsOut(
        transaction_id=transaction_id, txid=d.txid, fee_btc=d.fee_btc, script_type=d.script_type, source=d.source,
        inputs=[RichInputOut(position=i.position, address=i.address, amount_btc=i.amount_btc) for i in inputs],
        outputs=[RichInputOut(position=o.position, address=o.address, amount_btc=o.amount_btc) for o in outputs],
        flow=None if flow is None else FlowOut(src_ip=flow.src_ip, dst_ip=flow.dst_ip, src_port=flow.src_port, dst_port=flow.dst_port,
                                               geo_country=flow.geo_country, asn=flow.asn, asn_org=flow.asn_org,
                                               is_synthetic=flow.is_synthetic, origin=flow.origin))


@router.get("/api/geoip/status")
def geoip_status(geo: GeoIP = Depends(get_geoip)) -> dict[str, Any]:
    """Whether the offline GeoIP databases are available, their build dates and the required attribution."""
    return geo.status()
