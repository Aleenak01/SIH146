"""SQLite engine and session handling (SQLAlchemy). One `Database` object per application instance."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: Engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")     # SQLite ignores foreign keys unless asked
            cur.execute("PRAGMA journal_mode=WAL")    # readers do not block the monitor's writes
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    def init_db(self) -> None:
        """Create any missing tables. Additive only: existing tables and rows are never dropped."""
        if self.url.startswith("sqlite:///"):
            from pathlib import Path

            Path(self.url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._factory()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Commit on success, roll back on error."""
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
