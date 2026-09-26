"""
Rich transaction records (Phase 1): the record model and parsers for four formats -- CSV, JSON, JSONL and XML.

A rich record describes one transaction at address level: txid, fee, script type, the input and output addresses with their
amounts, and a (synthetic) network flow with its GeoIP fields. All formats are parsed into plain dictionaries first and then
validated one by one by `RichTransactionIn`, so a bad record is reported with its index and reason and never stops the others.

Formats
  csv    header row; columns are the record fields; the four list fields (input_addresses, input_amounts, output_addresses,
         output_amounts) are JSON arrays inside the cell.
  json   an array of records, or an object {"transactions": [ ... ]}.
  jsonl  one JSON record per line.
  xml    <transactions><transaction><txid/>...<input_addresses><address/>...</input_addresses>
         <input_amounts><amount/>...</input_amounts>...</transaction></transactions>. DTDs, entities and external references are
         rejected (defusedxml), which stops entity-expansion and external-file attacks.

Every record is SYNTHETIC in this project. `sender_wallet`, `receiver_wallet` and `amount_btc` are optional and only used to
feed the flat wallet-level view of the existing pipeline; they are never used for address-level analysis.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..models import RICH_SCRIPT_TYPES
from ..schemas import WALLET_PATTERN

FORMATS = ("csv", "json", "jsonl", "xml")
MAX_RECORDS = 20_000
MAX_BYTES = 30 * 1024 * 1024
BALANCE_TOLERANCE_SATS = 1
SAT = 100_000_000
LIST_FIELDS = ("input_addresses", "input_amounts", "output_addresses", "output_amounts")
REQUIRED_COLUMNS = ("txid", "timestamp", "fee_btc", "script_type") + LIST_FIELDS
ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class FormatError(ValueError):
    """The whole input is unusable (not valid JSON/CSV/XML, forbidden XML constructs, too large, wrong structure)."""


def to_sats(x: float) -> int:
    return int(round(x * SAT))


class RichTransactionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    timestamp: datetime
    txid: str
    legacy_transaction_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:\-]+$")
    sender_wallet: str | None = Field(default=None, min_length=1, max_length=128, pattern=WALLET_PATTERN)
    receiver_wallet: str | None = Field(default=None, min_length=1, max_length=128, pattern=WALLET_PATTERN)
    amount_btc: float | None = Field(default=None, gt=0, le=21_000_000, allow_inf_nan=False)
    fee_btc: float = Field(ge=0, le=21_000_000, allow_inf_nan=False)
    script_type: str
    input_addresses: list[str] = Field(min_length=1, max_length=10_000)
    input_amounts: list[float] = Field(min_length=1, max_length=10_000)
    output_addresses: list[str] = Field(min_length=1, max_length=10_000)
    output_amounts: list[float] = Field(min_length=1, max_length=10_000)
    src_ip: str | None = None
    dst_ip: str | None = None
    src_port: int | None = Field(default=None, ge=0, le=65535)
    dst_port: int | None = Field(default=None, ge=0, le=65535)
    geo_country: str | None = Field(default=None, min_length=2, max_length=2)
    asn: int | None = Field(default=None, ge=0)
    asn_org: str | None = Field(default=None, max_length=200)

    # ---- normalisation: empty cells (CSV / XML) mean "not given" ---------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _empty_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: (None if isinstance(v, str) and v.strip() == "" and k not in LIST_FIELDS else v) for k, v in data.items()}
        return data

    @field_validator("timestamp")
    @classmethod
    def _to_naive_utc(cls, v: datetime) -> datetime:
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo is not None else v

    @field_validator("txid")
    @classmethod
    def _txid(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", v):
            raise ValueError("txid must be 64 hexadecimal characters")
        return v.lower()

    @field_validator("script_type")
    @classmethod
    def _script(cls, v: str) -> str:
        v = v.lower()
        if v not in RICH_SCRIPT_TYPES:
            raise ValueError(f"script_type must be one of {', '.join(RICH_SCRIPT_TYPES)}")
        return v

    @field_validator("input_addresses", "output_addresses")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        for a in v:
            if not isinstance(a, str) or not ADDRESS_PATTERN.match(a.strip()):
                raise ValueError("addresses must be 1-128 characters from A-Z a-z 0-9 _ . : -")
        return [a.strip() for a in v]

    @field_validator("input_amounts", "output_amounts")
    @classmethod
    def _amounts(cls, v: list[float]) -> list[float]:
        for x in v:
            if x != x or x in (float("inf"), float("-inf")) or x > 21_000_000:
                raise ValueError("amounts must be finite numbers no larger than 21,000,000 BTC")
            if to_sats(x) < 1:
                raise ValueError("every amount must be at least 1 satoshi (0.00000001) and positive")
        return v

    @field_validator("src_ip", "dst_ip")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            return str(ipaddress.ip_address(v.strip()))
        except ValueError:
            raise ValueError("not a valid IP address") from None

    @field_validator("geo_country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.fullmatch(r"[A-Za-z]{2}", v):
            raise ValueError("geo_country must be a 2-letter ISO code")
        return v.upper()

    @model_validator(mode="after")
    def _consistency(self) -> "RichTransactionIn":
        if len(self.input_addresses) != len(self.input_amounts):
            raise ValueError(f"input_addresses ({len(self.input_addresses)}) and input_amounts ({len(self.input_amounts)}) differ in length")
        if len(self.output_addresses) != len(self.output_amounts):
            raise ValueError(f"output_addresses ({len(self.output_addresses)}) and output_amounts ({len(self.output_amounts)}) differ in length")
        inputs, outputs, fee = sum(map(to_sats, self.input_amounts)), sum(map(to_sats, self.output_amounts)), to_sats(self.fee_btc)
        if abs(inputs - outputs - fee) > BALANCE_TOLERANCE_SATS:
            raise ValueError(f"value does not balance: inputs {inputs} sat != outputs {outputs} sat + fee {fee} sat (tolerance {BALANCE_TOLERANCE_SATS} sat)")
        if self.sender_wallet and self.receiver_wallet and self.sender_wallet == self.receiver_wallet:
            raise ValueError("sender_wallet and receiver_wallet must differ")
        if self.amount_btc is not None and to_sats(self.amount_btc) not in set(map(to_sats, self.output_amounts)):
            raise ValueError("amount_btc must equal one of the output amounts (the payment to the receiver)")
        return self

    @property
    def has_flow(self) -> bool:
        return any(x is not None for x in (self.src_ip, self.dst_ip, self.src_port, self.dst_port))


# ---------------------------------------------------------------------------------------------------------------------
# parsers: bytes/text -> [(index, raw dict)] and per-record parse errors
# ---------------------------------------------------------------------------------------------------------------------
Raw = list[tuple[int, dict[str, Any]]]
Rejected = list[dict[str, Any]]


def _text(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    if len(data) > MAX_BYTES:
        raise FormatError(f"input is larger than the {MAX_BYTES // (1024 * 1024)} MB limit")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FormatError("input is not valid UTF-8 text") from None


def _cap(n: int) -> None:
    if n > MAX_RECORDS:
        raise FormatError(f"more than {MAX_RECORDS} records in one request; split the file")


def parse_csv(text: str) -> tuple[Raw, Rejected]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise FormatError(f"CSV header is missing required column(s): {', '.join(missing)}")
    raw: Raw = []
    rejected: Rejected = []
    for i, row in enumerate(reader):
        _cap(i + 1)
        if None in row or any(v is None for v in row.values()):
            rejected.append({"index": i, "error": "row has a different number of cells than the header"})
            continue
        try:
            for f in LIST_FIELDS:
                row[f] = json.loads(row[f])
                if not isinstance(row[f], list):
                    raise ValueError(f"{f} must be a JSON array")
        except ValueError as e:
            rejected.append({"index": i, "error": f"list column is not a JSON array: {e}"[:200]})
            continue
        raw.append((i, dict(row)))
    return raw, rejected


def _from_items(items: list[Any]) -> tuple[Raw, Rejected]:
    _cap(len(items))
    raw: Raw = []
    rejected: Rejected = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            raw.append((i, item))
        else:
            rejected.append({"index": i, "error": "record must be a JSON object"})
    return raw, rejected


def parse_json(text: str) -> tuple[Raw, Rejected]:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise FormatError(f"not valid JSON: {e}"[:200]) from None
    if isinstance(data, dict) and isinstance(data.get("transactions"), list):
        data = data["transactions"]
    if not isinstance(data, list):
        raise FormatError('JSON must be an array of records or an object {"transactions": [...]}')
    return _from_items(data)


def parse_jsonl(text: str) -> tuple[Raw, Rejected]:
    raw: Raw = []
    rejected: Rejected = []
    n = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        _cap(n + 1)
        try:
            item = json.loads(line)
        except ValueError:
            rejected.append({"index": n, "error": "line is not valid JSON"})
        else:
            if isinstance(item, dict):
                raw.append((n, item))
            else:
                rejected.append({"index": n, "error": "record must be a JSON object"})
        n += 1
    return raw, rejected


def parse_xml(text: str) -> tuple[Raw, Rejected]:
    from defusedxml import DefusedXmlException
    from defusedxml.ElementTree import ParseError, fromstring

    try:
        root = fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise FormatError(f"XML rejected: {type(e).__name__} (DTDs, entities and external references are not allowed)") from None
    except ParseError as e:
        raise FormatError(f"not valid XML: {e}"[:200]) from None
    if root.tag != "transactions":
        raise FormatError("XML root element must be <transactions>")
    children = list(root)
    _cap(len(children))
    raw: Raw = []
    rejected: Rejected = []
    for i, tx in enumerate(children):
        if tx.tag != "transaction":
            rejected.append({"index": i, "error": f"unexpected element <{tx.tag}>; expected <transaction>"})
            continue
        rec: dict[str, Any] = {}
        for el in tx:
            if el.tag in LIST_FIELDS:
                rec[el.tag] = [(c.text or "").strip() for c in el]
                if el.tag.endswith("amounts"):
                    try:
                        rec[el.tag] = [float(x) for x in rec[el.tag]]
                    except ValueError:
                        rejected.append({"index": i, "error": f"{el.tag} contains a value that is not a number"})
                        rec = {}
                        break
            else:
                rec[el.tag] = (el.text or "").strip()
        if rec:
            raw.append((i, rec))
    return raw, rejected


def parse(data: bytes | str, fmt: str) -> tuple[Raw, Rejected]:
    """Parse a whole request body. Raises FormatError if the input as a whole is unusable."""
    if fmt not in FORMATS:
        raise FormatError(f"format must be one of {', '.join(FORMATS)}")
    text = _text(data)
    return {"csv": parse_csv, "json": parse_json, "jsonl": parse_jsonl, "xml": parse_xml}[fmt](text)


def validate_records(raw: Raw) -> tuple[list[tuple[int, RichTransactionIn]], Rejected]:
    """Validate parsed dictionaries one by one. Returns the valid records with their index, and the rejected ones with the reason."""
    valid: list[tuple[int, RichTransactionIn]] = []
    rejected: Rejected = []
    for i, rec in raw:
        try:
            valid.append((i, RichTransactionIn.model_validate(rec)))
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'record'}: {err['msg']}" for err in e.errors())
            rejected.append({"index": i, "error": msg[:400]})
    return valid, rejected
