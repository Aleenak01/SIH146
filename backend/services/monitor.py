"""
Local continuous-monitoring service (a prototype, not a distributed streaming system).

A background thread repeats `tick()` every `interval` seconds:

    1. inbox     new .csv / .jsonl files in the inbox folder are validated and ingested
    2. stream    if the SYNTHETIC stream is enabled, new synthetic transactions are generated and ingested
    3. analysis  if new transactions arrived since the last completed analysis, and automatic analysis is on,
                 the whole pipeline is re-run (features -> Isolation Forest -> forensic rules -> fusion -> leads)

Transactions posted through POST /api/transactions are picked up the same way (step 3 detects that the data has
changed). Analysis therefore runs in micro-batches at the tick interval, not once per transaction: the model and
the percentile rules are population-relative and a full re-analysis takes several seconds.

The data source is always reported as synthetic. Nothing here contacts a network or a blockchain.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..analysis.ml_bridge import AnalysisError
from ..analysis.service import ANALYSIS_LOCK, AnalysisBusy, RunSummary, analysis_is_stale, latest_run, run_analysis
from ..config import Settings
from ..database import Database
from ..ingestion.inbox import process_inbox
from ..ingestion.synthetic_stream import StreamUnavailable, SyntheticStreamSource
from ..models import InvestigativeLead, Transaction, Wallet, utcnow
from .ingest import ingest

MIN_INTERVAL, MAX_INTERVAL = 1.0, 3600.0
MIN_RATE, MAX_RATE = 1.0, 600.0
SOURCE = "synthetic"


class MonitorConfigError(ValueError):
    pass


class Monitor:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings
        self.interval = float(settings.monitor_interval)
        self.auto_analysis = True
        self.stream_enabled = False
        self.stream_rate = float(settings.stream_rate_per_minute)
        self._stream: SyntheticStreamSource | None = None
        self._stream_credit = 0.0
        self._last_stream_tick: float | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self.started_at: datetime | None = None
        self.ticks = 0
        self.last_error: str | None = None
        self.last_ingest_at: datetime | None = None
        self.last_ingest_origin: str | None = None
        self.ingested = {"api": 0, "inbox": 0, "stream": 0}
        self.inbox_rejected = 0
        self.last_analysis_summary: RunSummary | None = None
        self._failed_at_count: int | None = None        # do not retry a failing analysis until the data changes
        self.last_scenario: dict[str, Any] | None = None

    # ---- lifecycle ------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.started_at = utcnow()
        self._thread = threading.Thread(target=self._loop, name="sih146-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:                                # the monitor must never die from one bad tick
                self._record_error(e)
            self._stop.wait(self.interval)

    def _record_error(self, e: Exception) -> None:
        with self._state_lock:
            self.last_error = f"{type(e).__name__}: {e}"[:500]

    # ---- one monitoring cycle -----------------------------------------------------------------------------
    def tick(self) -> dict[str, Any]:
        """Run one cycle now. Safe to call from tests or the API; cycles never overlap."""
        with self._tick_lock:
            self.ticks += 1
            result: dict[str, Any] = {"inbox": 0, "stream": 0, "analysis": None}

            inbox = process_inbox(self.db, self.settings.inbox_dir)
            if inbox.inserted:
                self.record_ingest(inbox.inserted, "inbox")
            self.inbox_rejected += inbox.rejected
            result["inbox"] = inbox.inserted

            if self.stream_enabled:
                result["stream"] = self._stream_step()

            if self.auto_analysis:
                result["analysis"] = self._analysis_step()
            return result

    def _stream_step(self) -> int:
        try:
            if self._stream is None:
                self._stream = SyntheticStreamSource(self.db, self.settings.stream_seed)
            now_m = time.monotonic()
            elapsed = self.interval if self._last_stream_tick is None else now_m - self._last_stream_tick
            self._last_stream_tick = now_m
            self._stream_credit += self.stream_rate * elapsed / 60.0
            n = int(self._stream_credit)
            if n <= 0:
                return 0
            self._stream_credit -= n
            return self._ingest_stream(self._stream.generate(n, _now()))
        except StreamUnavailable as e:
            self._record_error(e)
            return 0

    def _ingest_stream(self, items) -> int:
        with self.db.transaction() as s:
            out = ingest(s, items, SOURCE)
        if out.inserted:
            self.record_ingest(out.inserted, "stream")
        return out.inserted

    def _analysis_step(self) -> dict[str, Any] | None:
        if ANALYSIS_LOCK.locked():
            return None
        with self.db.session() as s:
            stale = analysis_is_stale(s, SOURCE)
            count = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == SOURCE)) or 0
        if not stale or self._failed_at_count == count:
            return None
        try:
            summary = run_analysis(self.db, SOURCE, trigger="monitor")
        except AnalysisBusy:
            return None
        except AnalysisError as e:
            self._failed_at_count = count
            self._record_error(e)
            return None
        except Exception as e:
            self._failed_at_count = count
            self._record_error(e)
            return None
        self._failed_at_count = None
        with self._state_lock:
            self.last_analysis_summary = summary
            self.last_error = None
        return summary.as_dict()

    # ---- bookkeeping used by the API ----------------------------------------------------------------------
    def record_ingest(self, n: int, origin: str) -> None:
        with self._state_lock:
            self.ingested[origin] = self.ingested.get(origin, 0) + n
            self.last_ingest_at, self.last_ingest_origin = utcnow(), origin

    def configure(self, *, interval: float | None = None, auto_analysis: bool | None = None,
                  stream_enabled: bool | None = None, stream_rate: float | None = None) -> None:
        if interval is not None:
            if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
                raise MonitorConfigError(f"interval_seconds must be between {MIN_INTERVAL:g} and {MAX_INTERVAL:g}")
            self.interval = float(interval)
        if stream_rate is not None:
            if not MIN_RATE <= stream_rate <= MAX_RATE:
                raise MonitorConfigError(f"stream_rate_per_minute must be between {MIN_RATE:g} and {MAX_RATE:g}")
            self.stream_rate = float(stream_rate)
        if auto_analysis is not None:
            self.auto_analysis = bool(auto_analysis)
        if stream_enabled is not None:
            if stream_enabled and self._stream is None:
                try:
                    self._stream = SyntheticStreamSource(self.db, self.settings.stream_seed)
                except StreamUnavailable as e:
                    raise MonitorConfigError(str(e)) from e
            self.stream_enabled = bool(stream_enabled)
            self._last_stream_tick = None if stream_enabled else self._last_stream_tick

    def inject_fan_out_scenario(self) -> dict[str, Any]:
        """Ingest the optional demo scenario (a quiet wallet fans out to many new wallets). Labelled synthetic."""
        try:
            stream = self._stream or SyntheticStreamSource(self.db, self.settings.stream_seed)
            self._stream = stream
            stream.refresh()
            wallet, items = stream.fan_out_scenario(_now())
        except StreamUnavailable as e:
            raise MonitorConfigError(str(e)) from e
        n = self._ingest_stream(items)
        info = {"scenario": "fan_out", "wallet": wallet, "transactions": n, "synthetic": True,
                "note": "Demo scenario: ordinary synthetic transactions; the analysis judges them like any other data."}
        self.last_scenario = info
        return info

    # ---- status ---------------------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        with self.db.session() as s:
            transactions = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == SOURCE)) or 0
            wallets = s.scalar(select(func.count()).select_from(Wallet).where(Wallet.source == SOURCE)) or 0
            last_tx = s.scalar(select(func.max(Transaction.timestamp)).where(Transaction.source == SOURCE))
            run = latest_run(s, SOURCE)
            stale = analysis_is_stale(s, SOURCE)
            leads = s.scalar(select(func.count()).select_from(InvestigativeLead).where(InvestigativeLead.source == SOURCE)) or 0
            new_leads = (s.scalar(select(func.count()).select_from(InvestigativeLead).where(
                InvestigativeLead.source == SOURCE, InvestigativeLead.first_flagged_at >= run.started_at)) or 0) if run else 0
            last_analysis_at, run_id, run_trigger = (run.finished_at, run.run_id, run.trigger) if run else (None, None, None)
        with self._state_lock:
            stream_label = "Synthetic stream" if self.stream_enabled else "Synthetic dataset"
            return {
                "status": "active" if self.running else "stopped",
                "data_source": {
                    "kind": "synthetic", "label": stream_label, "is_real_data": False,
                    "note": "Synthetic demo data. No real blockchain data is being monitored.",
                },
                "interval_seconds": self.interval,
                "auto_analysis": self.auto_analysis,
                "analysis_mode": "micro-batch: the whole population is re-analysed when new transactions have arrived",
                "started_at": self.started_at, "ticks": self.ticks,
                "transactions_total": transactions, "wallets_total": wallets,
                "last_transaction_at": last_tx, "last_ingest_at": self.last_ingest_at, "last_ingest_origin": self.last_ingest_origin,
                "ingested_since_start": dict(self.ingested),
                "last_analysis_at": last_analysis_at, "last_analysis_run_id": run_id, "last_analysis_trigger": run_trigger,
                "analysis_in_progress": ANALYSIS_LOCK.locked(), "analysis_stale": stale,
                "leads_total": leads, "new_leads_last_run": new_leads,
                "stream": {"enabled": self.stream_enabled, "rate_per_minute": self.stream_rate, "seed": self.settings.stream_seed, "last_scenario": self.last_scenario},
                "inbox": {"path": self.settings.inbox_dir.name, "rejected_rows_since_start": self.inbox_rejected},
                "last_error": self.last_error,
            }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
