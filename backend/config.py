"""
Runtime settings from environment variables, with an optional project-root `.env` file.

Only local, non-secret defaults live here. Nothing in this module needs the internet, and the
application runs in synthetic/offline mode with no configuration at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    db_path: Path
    dataset_csv: Path
    host: str
    port: int
    cors_origins: list[str] = field(default_factory=list)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


def load_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")
    env = os.environ.get
    return Settings(
        db_path=_resolve(env("SIH146_DB_PATH", "data/sih146.db")),
        dataset_csv=_resolve(env("SIH146_DATASET_CSV", "dataset/synthetic_bitcoin_transactions.csv")),
        host=env("SIH146_HOST", "127.0.0.1"),
        port=int(env("SIH146_PORT", "8000")),
        cors_origins=[o.strip() for o in env("SIH146_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    )
