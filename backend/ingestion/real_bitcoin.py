"""
Optional real Bitcoin source: recent confirmed transactions from an Esplora-compatible public API
(https://github.com/Blockstream/esplora/blob/master/API.md, e.g. https://blockstream.info/api).

Scope and honesty rules
  * Read-only GET requests, only when a fetch is explicitly requested and the source is enabled in the environment.
    Nothing here runs by itself; the monitor never uses it. Without it the application works fully offline.
  * Nothing is invented. If the API cannot be reached, is rate limited past the retries, or answers with something
    unexpected, `SourceUnavailable` is raised and nothing is stored.
  * The credentials (an optional API key) come from the environment only, are sent only as a Bearer header, and never
    appear in an exception message, a log line or an API response.
  * Real transactions are labelled source = 'real_bitcoin' and never receive synthetic IP / device / session data.

How a Bitcoin transaction becomes the internal (sender -> receiver, amount) model
  A real transaction is UTXO based: many input addresses and many output addresses, and no "sender" or "receiver".
  The internal model has exactly one of each, so a documented simplification is applied to every confirmed,
  non-coinbase transaction:

    sender     the input address that contributed the largest value (ties: the lexicographically smaller address).
               The other input addresses are not attributed. This is NOT entity attribution or a co-spend cluster.
    receivers  every output address that is not also an input address. Outputs back to an input address are treated
               as change and dropped. Outputs without an address (OP_RETURN, non-standard) are dropped.
    transfers  one internal transfer per receiver: sender -> receiver, amount = the sum of that address's outputs.
    counts     input_count / output_count are the real numbers of inputs and outputs of the transaction.
    ids        transaction_id = 'btc:<txid>:<first output index to that receiver>', source_ref = '<txid>:<index>'.

  Skipped, and counted in the report: unconfirmed, coinbase, no input address, no output to another address
  (consolidations / pure change), and anything the internal validation rejects.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from ..schemas import TransactionIn
from .base import SourcedTransaction, TransactionSource

PAGE_SIZE = 25                    # Esplora returns block transactions 25 at a time (start_index must be a multiple of 25)
USER_AGENT = "sih146-prototype/0.2 (read-only research client)"


class SourceNotConfigured(RuntimeError):
    """The real Bitcoin source is disabled or its configuration is unusable."""


class SourceUnavailable(RuntimeError):
    """The external API could not be reached, refused the request, or returned unusable data."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


Transport = Callable[[str, dict[str, str], float], HttpResponse]


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:            # noqa: S310 - scheme is validated by validate_base_url
            return HttpResponse(r.status, r.read(), {k.lower(): v for k, v in r.headers.items()})
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, e.read() or b"", {k.lower(): v for k, v in (e.headers or {}).items()})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceUnavailable(f"Could not reach the Bitcoin data source ({type(e).__name__}).") from None


def validate_base_url(url: str) -> str:
    """http(s) only, a host, and no credentials embedded in the URL."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceNotConfigured("SIH146_REAL_BITCOIN_BASE_URL must be an http(s) URL.")
    if parts.username or parts.password:
        raise SourceNotConfigured("Do not put credentials in SIH146_REAL_BITCOIN_BASE_URL; use SIH146_REAL_BITCOIN_API_KEY.")
    return url.rstrip("/")


def public_url(url: str) -> str:
    """The URL without credentials, query string or fragment, safe to show."""
    parts = urlsplit(url or "")
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


class EsploraClient:
    """Minimal Esplora client: chain tip, block hash by height, and a block's transactions."""

    def __init__(self, base_url: str, *, api_key: str | None = None, timeout: float = 15.0, delay: float = 0.3,
                 transport: Transport = urllib_transport, sleep: Callable[[float], None] = time.sleep, max_retries: int = 3) -> None:
        self.base_url = validate_base_url(base_url)
        self._api_key = api_key
        self.timeout, self.delay, self.max_retries = timeout, delay, max_retries
        self._transport, self._sleep = transport, sleep
        self.requests = 0

    def _get(self, path: str) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            if self.requests and self.delay:
                self._sleep(self.delay)
            self.requests += 1
            try:
                r = self._transport(url, headers, self.timeout)
            except SourceUnavailable:                       # timeout / connection problem: retry with backoff, then give up
                if attempt < self.max_retries:
                    self._sleep(2.0 ** attempt)
                    continue
                raise
            if r.status == 200:
                return r.body
            if r.status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = r.headers.get("retry-after", "")
                self._sleep(min(30.0, float(retry_after)) if retry_after.replace(".", "", 1).isdigit() else 2.0 ** attempt)
                continue
            if r.status == 429:
                raise SourceUnavailable("The Bitcoin data source is rate limiting requests; try again later or fetch fewer blocks.")
            if r.status == 404:
                raise SourceUnavailable(f"The Bitcoin data source has no such resource ({path}).")
            raise SourceUnavailable(f"The Bitcoin data source answered HTTP {r.status}.")
        raise SourceUnavailable("The Bitcoin data source did not answer.")          # pragma: no cover

    def _json(self, path: str) -> Any:
        try:
            return json.loads(self._get(path))
        except ValueError:
            raise SourceUnavailable("The Bitcoin data source returned data that is not valid JSON.") from None

    def tip_height(self) -> int:
        text = self._get("/blocks/tip/height").decode("ascii", "replace").strip()
        if not text.isdigit():
            raise SourceUnavailable("The Bitcoin data source returned an unexpected chain height.")
        return int(text)

    def block_hash(self, height: int) -> str:
        text = self._get(f"/block-height/{int(height)}").decode("ascii", "replace").strip()
        if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
            raise SourceUnavailable("The Bitcoin data source returned an unexpected block hash.")
        return text

    def block_txs(self, block_hash: str, start_index: int) -> list[dict[str, Any]]:
        if len(block_hash) != 64 or any(c not in "0123456789abcdef" for c in block_hash):
            raise ValueError("block_hash must be 64 hex characters")
        data = self._json(f"/block/{block_hash}/txs/{int(start_index)}")
        if not isinstance(data, list):
            raise SourceUnavailable("The Bitcoin data source returned an unexpected block listing.")
        return data


# ---------------------------------------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------------------------------------
def normalize_tx(tx: dict[str, Any]) -> tuple[list[SourcedTransaction], str | None]:
    """One Esplora transaction -> internal transfers, or ([], reason) if it is skipped. See the module docstring."""
    txid = tx.get("txid")
    status = tx.get("status") or {}
    if not isinstance(txid, str) or len(txid) != 64:
        return [], "invalid"
    if not status.get("confirmed") or not isinstance(status.get("block_time"), int):
        return [], "unconfirmed"
    vin, vout = tx.get("vin") or [], tx.get("vout") or []
    if not vin or any(v.get("is_coinbase") for v in vin):
        return [], "coinbase"

    inputs: Counter[str] = Counter()
    for v in vin:
        prev = v.get("prevout") or {}
        addr, value = prev.get("scriptpubkey_address"), prev.get("value")
        if addr and isinstance(value, int) and value > 0:
            inputs[addr] += value
    if not inputs:
        return [], "no_input_address"
    sender = min(inputs, key=lambda a: (-inputs[a], a))

    outputs: dict[str, list[int]] = {}
    for i, o in enumerate(vout):
        addr, value = o.get("scriptpubkey_address"), o.get("value")
        if not addr or not isinstance(value, int) or value <= 0 or addr in inputs:    # no address / change back to an input
            continue
        entry = outputs.setdefault(addr, [i, 0])
        entry[1] += value
    if not outputs:
        return [], "no_external_output"

    when = datetime.fromtimestamp(status["block_time"], tz=timezone.utc)
    out: list[SourcedTransaction] = []
    for receiver, (index, sats) in sorted(outputs.items(), key=lambda kv: kv[1][0]):
        try:
            payload = TransactionIn(
                transaction_id=f"btc:{txid}:{index}", timestamp=when, sender_wallet=sender, receiver_wallet=receiver,
                amount_btc=sats / 100_000_000, input_count=min(len(vin), 10_000), output_count=min(max(len(vout), 1), 10_000))
        except ValueError:
            return [], "invalid"
        out.append(SourcedTransaction(payload, source_ref=f"{txid}:{index}"))
    return out, None


@dataclass
class FetchReport:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    transactions_examined: int = 0
    transfers: int = 0
    skipped: Counter = field(default_factory=Counter)
    requests: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"blocks": self.blocks, "transactions_examined": self.transactions_examined, "transfers": self.transfers,
                "skipped": dict(self.skipped), "requests": self.requests}


class RealBitcoinSource(TransactionSource):
    """
    Reads confirmed transactions from the newest `blocks` blocks (or from explicit `heights`). From each block only the
    first `max_tx_per_block` transactions are read: that is NOT a random sample (block order follows fee and
    dependency order), only a bounded slice. The result is all-or-nothing: any failure raises and nothing is returned.
    """

    source = "real_bitcoin"

    def __init__(self, client: EsploraClient, *, blocks: int = 1, heights: list[int] | None = None,
                 max_blocks: int = 5, max_tx_per_block: int = 500) -> None:
        wanted = len(heights) if heights else blocks
        if wanted < 1 or wanted > max_blocks:
            raise ValueError(f"blocks must be between 1 and {max_blocks}")
        if not 1 <= max_tx_per_block:
            raise ValueError("max_tx_per_block must be at least 1")
        self.client, self.blocks, self.heights = client, blocks, heights
        self.max_tx_per_block = max_tx_per_block
        self.last_report: FetchReport | None = None

    def fetch(self) -> list[SourcedTransaction]:
        report = FetchReport()
        heights = self.heights or [self.client.tip_height() - i for i in range(self.blocks)]
        items: list[SourcedTransaction] = []
        for height in heights:
            block_hash = self.client.block_hash(height)
            examined, start = 0, 0
            while examined < self.max_tx_per_block:
                page = self.client.block_txs(block_hash, start)
                for tx in page:
                    if examined >= self.max_tx_per_block:
                        break
                    examined += 1
                    transfers, reason = normalize_tx(tx)
                    if reason:
                        report.skipped[reason] += 1
                    items.extend(transfers)
                if len(page) < PAGE_SIZE:
                    break
                start += PAGE_SIZE
            report.blocks.append({"height": height, "hash": block_hash, "transactions_examined": examined})
            report.transactions_examined += examined
        report.transfers = len(items)
        report.requests = self.client.requests
        self.last_report = report
        return items
