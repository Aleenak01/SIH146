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
    # Monitoring (synthetic/local): see backend/services/monitor.py
    monitor_autostart: bool = True             # start the monitor (inbox watcher + automatic analysis) with the server
    monitor_interval: float = 5.0              # seconds between monitor ticks (micro-batch analysis interval)
    inbox_dir: Path = PROJECT_ROOT / "data" / "stream_inbox"
    stream_seed: int = 146                     # seed for the synthetic stream, so a demo is reproducible
    stream_rate_per_minute: float = 30.0
    network_csv: Path = PROJECT_ROOT / "data" / "synthetic_network_observations.csv"   # SYNTHETIC network observations
    network_seed: int = 148
    # Optional real Bitcoin source (Esplora-compatible public API, e.g. blockstream.info). OFF unless enabled here; the
    # application never contacts it on its own (the monitor does not use it) and works fully offline without it.
    real_bitcoin_enabled: bool = False
    real_bitcoin_base_url: str = "https://blockstream.info/api"
    real_bitcoin_api_key: str | None = field(default=None, repr=False)      # optional; never returned by the API or logged
    real_bitcoin_timeout: float = 15.0
    real_bitcoin_request_delay: float = 0.3         # seconds between requests (be polite to public APIs)
    real_bitcoin_max_blocks: int = 5                # most blocks one fetch may read
    real_bitcoin_max_tx_per_block: int = 500        # most transactions read from each block

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
        monitor_autostart=env("SIH146_MONITOR_AUTOSTART", "true").strip().lower() in ("1", "true", "yes", "on"),
        monitor_interval=max(1.0, float(env("SIH146_MONITOR_INTERVAL", "5"))),
        inbox_dir=_resolve(env("SIH146_INBOX_DIR", "data/stream_inbox")),
        stream_seed=int(env("SIH146_STREAM_SEED", "146")),
        stream_rate_per_minute=float(env("SIH146_STREAM_RATE_PER_MINUTE", "30")),
        network_csv=_resolve(env("SIH146_NETWORK_CSV", "data/synthetic_network_observations.csv")),
        network_seed=int(env("SIH146_NETWORK_SEED", "148")),
        real_bitcoin_enabled=env("SIH146_REAL_BITCOIN_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"),
        real_bitcoin_base_url=env("SIH146_REAL_BITCOIN_BASE_URL", "https://blockstream.info/api").strip().rstrip("/"),
        real_bitcoin_api_key=(env("SIH146_REAL_BITCOIN_API_KEY", "").strip() or None),
        real_bitcoin_timeout=max(1.0, float(env("SIH146_REAL_BITCOIN_TIMEOUT", "15"))),
        real_bitcoin_request_delay=max(0.0, float(env("SIH146_REAL_BITCOIN_REQUEST_DELAY", "0.3"))),
        real_bitcoin_max_blocks=max(1, int(env("SIH146_REAL_BITCOIN_MAX_BLOCKS", "5"))),
        real_bitcoin_max_tx_per_block=max(1, int(env("SIH146_REAL_BITCOIN_MAX_TX_PER_BLOCK", "500"))),
    )
