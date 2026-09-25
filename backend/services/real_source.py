"""
The optional real Bitcoin source, as seen by the API: configuration status, what a fetch does, which of the model
features can and cannot be computed from real data, and the fetch itself.

Real data is stored with source = 'real_bitcoin', analysed only when an analysis for that source is requested, and never
mixed with the synthetic data or given synthetic network observations.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, union_all

from ..config import Settings
from ..database import Database
from ..ingestion.real_bitcoin import EsploraClient, RealBitcoinSource, SourceNotConfigured, Transport, public_url, urllib_transport
from ..models import FEATURE_COLUMNS, Transaction, Wallet, utcnow
from .ingest import ingest

SOURCE = "real_bitcoin"
FETCH_LOCK = threading.Lock()

# The four features that need at least two transactions of the wallet (they are time gaps). Verified against the
# feature-engineering code in tests. The other 14 are computable for any wallet with at least one transaction.
NEEDS_TWO_TRANSACTIONS = ("time_since_previous_tx", "avg_transaction_interval", "transaction_frequency", "dormancy_duration")

NORMALIZATION_RULES = [
    "Only confirmed, non-coinbase transactions are used.",
    "sender = the input address with the largest input value (the other inputs are not attributed; this is not entity attribution).",
    "receivers = every output address that is not also an input address (change back to an input is dropped; OP_RETURN and address-less outputs are dropped).",
    "One internal transfer per receiver: amount = the sum of that address's outputs. input_count and output_count are the real counts.",
    "Only the first N transactions of each block are read (bounded slice, not a random sample).",
]

NETWORK_METADATA = {
    "ip_address": "Not available: Bitcoin transactions do not contain an IP address.",
    "device": "Not available: Bitcoin transactions do not contain device information.",
    "session": "Not available: Bitcoin transactions do not contain session information.",
}


class FetchBusy(RuntimeError):
    """A fetch is already running."""


@dataclass
class RealSourceState:
    """What this server process has done with the real source (nothing persists beyond the stored transactions)."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    last_fetch: dict[str, Any] | None = None
    last_error: str | None = None


def feature_availability() -> list[dict[str, str]]:
    out = []
    for name in FEATURE_COLUMNS:
        if name in NEEDS_TWO_TRANSACTIONS:
            out.append({"feature": name, "availability": "needs_two_transactions",
                        "note": "Computable only for a wallet with at least two transactions in the fetched data; otherwise it is unavailable and the wallet is left unscored (never filled in)."})
        else:
            out.append({"feature": name, "availability": "available",
                        "note": "Computed from the fetched transfers. It describes the fetched slice of the chain only, not the wallet's full history."})
    return out


def _wallet_activity(session) -> dict[str, int]:
    legs = union_all(
        select(Transaction.sender_wallet.label("w")).where(Transaction.source == SOURCE),
        select(Transaction.receiver_wallet.label("w")).where(Transaction.source == SOURCE),
    ).subquery()
    counts = select(legs.c.w, func.count().label("n")).group_by(legs.c.w).subquery()
    total = session.scalar(select(func.count()).select_from(counts)) or 0
    scorable = session.scalar(select(func.count()).select_from(counts).where(counts.c.n >= 2)) or 0
    return {"wallets": total, "wallets_with_two_or_more_transactions": scorable}


def status(settings: Settings, session, state: RealSourceState) -> dict[str, Any]:
    """Configuration (never any secret), stored data, feature availability and the normalization rules."""
    n_tx = session.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == SOURCE)) or 0
    first, last = session.execute(select(func.min(Transaction.timestamp), func.max(Transaction.timestamp)).where(Transaction.source == SOURCE)).one()
    enabled = settings.real_bitcoin_enabled
    return {
        "source": SOURCE,
        "status": "configured" if enabled else "not_configured",
        "label": ("Adapter ready; external source configured" if enabled else "Adapter ready; external source not configured"),
        "enabled": enabled,
        "base_url": public_url(settings.real_bitcoin_base_url),
        "api_key_configured": settings.real_bitcoin_api_key is not None,
        "limits": {"max_blocks": settings.real_bitcoin_max_blocks, "max_transactions_per_block": settings.real_bitcoin_max_tx_per_block,
                   "request_delay_seconds": settings.real_bitcoin_request_delay, "timeout_seconds": settings.real_bitcoin_timeout},
        "stored": {"transactions": n_tx, "first_transaction_at": first, "last_transaction_at": last, **_wallet_activity(session)},
        "last_fetch": state.last_fetch,
        "last_error": state.last_error,
        "how_to_enable": "Set SIH146_REAL_BITCOIN_ENABLED=true in your local .env and restart. Nothing contacts the network until a fetch is requested.",
        "data_separation": "Real transactions are stored with source = real_bitcoin, are not shown in the synthetic dashboards, and never receive synthetic IP / device / session data.",
        "normalization": NORMALIZATION_RULES,
        "features": feature_availability(),
        "network_metadata": NETWORK_METADATA,
    }


def fetch_and_ingest(db: Database, settings: Settings, state: RealSourceState, *, blocks: int = 1, heights: list[int] | None = None,
                     max_tx_per_block: int | None = None, transport: Transport | None = None, sleep=None) -> dict[str, Any]:
    """
    Read blocks from the configured API and store the resulting transfers. All-or-nothing: if the API fails, nothing is
    stored. Raises SourceNotConfigured (disabled), SourceUnavailable (API problem), ValueError (bad request).
    """
    if not settings.real_bitcoin_enabled:
        raise SourceNotConfigured("The real Bitcoin source is not enabled. Set SIH146_REAL_BITCOIN_ENABLED=true in .env and restart.")
    if not FETCH_LOCK.acquire(blocking=False):
        raise FetchBusy("A real Bitcoin fetch is already running.")
    try:
        per_block = min(max_tx_per_block or settings.real_bitcoin_max_tx_per_block, settings.real_bitcoin_max_tx_per_block)
        kwargs = {"sleep": sleep} if sleep is not None else {}
        client = EsploraClient(settings.real_bitcoin_base_url, api_key=settings.real_bitcoin_api_key, timeout=settings.real_bitcoin_timeout,
                               delay=settings.real_bitcoin_request_delay, transport=transport or urllib_transport, **kwargs)
        source = RealBitcoinSource(client, blocks=blocks, heights=heights, max_blocks=settings.real_bitcoin_max_blocks, max_tx_per_block=per_block)
        try:
            items = source.fetch()
        except Exception as e:
            with state.lock:
                state.last_error = f"{type(e).__name__}: {e}"[:300]
            raise
        with db.transaction() as s:
            outcome = ingest(s, items, SOURCE, observe=False)         # real data never gets synthetic observations
        result = {
            "fetched_at": utcnow().isoformat() + "Z", **source.last_report.as_dict(),
            "inserted": outcome.inserted, "duplicates": outcome.duplicates, "rejected": len(outcome.rejected),
            "rejected_examples": outcome.rejected[:5],
        }
        with state.lock:
            state.last_fetch, state.last_error = result, None
        return result
    finally:
        FETCH_LOCK.release()
