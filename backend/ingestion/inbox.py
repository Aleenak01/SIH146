"""
Inbox: a folder the monitor watches for new SYNTHETIC transaction files (.csv or .jsonl).

    data/stream_inbox/new_batch.csv     -> ingested, then moved to data/stream_inbox/processed/
                                          (rejected rows are listed in processed/new_batch.csv.rejected.txt)
    unreadable / oversized files        -> moved to data/stream_inbox/failed/ with a .reason.txt

CSV columns:  timestamp, sender_wallet, receiver_wallet, amount_btc [, input_count, output_count, transaction_id]
JSONL: one JSON object per line with the same keys.

Nothing here can crash the monitor: every problem is reported, never raised.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..database import Database
from ..schemas import TransactionIn
from ..services.ingest import ingest, validate_payloads
from .base import SourcedTransaction

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
REQUIRED = {"timestamp", "sender_wallet", "receiver_wallet", "amount_btc"}
ALLOWED = set(TransactionIn.model_fields)


@dataclass
class InboxResult:
    files: int = 0
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0
    failed_files: int = 0


def _read_rows(path: Path) -> list[tuple[int, object]]:
    """[(line_number, payload)] where payload is a dict, or an error string for a line that could not be parsed."""
    rows: list[tuple[int, object]] = []
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        for n, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                try:
                    rows.append((n, json.loads(line)))
                except json.JSONDecodeError as e:
                    rows.append((n, f"invalid JSON: {e.msg}"))
    else:
        reader = csv.DictReader(text.splitlines())
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing required column(s): {', '.join(sorted(missing))}")
        for n, rec in enumerate(reader, start=2):
            rows.append((n, {k: v for k, v in rec.items() if k in ALLOWED and v not in (None, "")}))
    if len(rows) > MAX_ROWS:
        raise ValueError(f"more than {MAX_ROWS} rows")
    return rows


def process_inbox(db: Database, inbox: Path, min_age_seconds: float = 1.0) -> InboxResult:
    result = InboxResult()
    if not inbox.is_dir():
        return result
    for path in sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".jsonl")):
        if time.time() - path.stat().st_mtime < min_age_seconds:
            continue                                            # still being written
        result.files += 1
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"file larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
            rows = _read_rows(path)
            notes: list[str] = []
            payloads, lines = [], []
            for line_no, payload in rows:
                if isinstance(payload, dict):
                    payloads.append(payload)
                    lines.append(line_no)
                else:
                    notes.append(f"line {line_no}: {payload}")
            valid, rejected = validate_payloads(payloads)
            notes += [f"line {lines[r['index']]}: {r['error']}" for r in rejected]
            items = [SourcedTransaction(tx, f"inbox:{path.name}:{lines[i]}") for i, tx in valid]
            with db.transaction() as session:
                out = ingest(session, items, "synthetic")
            notes += [f"line {lines[valid[r['index']][0]]}: {r['error']}" for r in out.rejected]
            result.inserted += out.inserted
            result.duplicates += out.duplicates
            result.rejected += len(notes)
            _archive(path, "processed", notes)
        except Exception as e:                                  # unreadable file, bad encoding, database error, ...
            result.failed_files += 1
            _archive(path, "failed", [f"{type(e).__name__}: {e}"])
    return result


def _archive(path: Path, folder: str, notes: list[str]) -> None:
    dest_dir = path.parent / folder
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"
    try:
        shutil.move(str(path), dest)
        if notes:
            dest.with_name(dest.name + (".rejected.txt" if folder == "processed" else ".reason.txt")).write_text("\n".join(notes) + "\n", encoding="utf-8")
    except OSError:
        pass                                                    # never let housekeeping stop the monitor
