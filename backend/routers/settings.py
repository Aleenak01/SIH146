from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..database import Database
from ..deps import get_db, get_monitor
from ..errors import AppError
from ..services import app_settings
from ..services.monitor import Monitor, MonitorConfigError

router = APIRouter(prefix="/api/settings", tags=["settings"])

REAL_BITCOIN = {
    "available": False,
    "configured": False,
    "status": "not_implemented",
    "note": "Real Bitcoin ingestion is not available in this build. All data is synthetic demo data.",
}


class SettingsIn(BaseModel):
    """Every field optional: send only what changes."""

    model_config = ConfigDict(extra="forbid")

    monitor_enabled: bool | None = None
    interval_seconds: float | None = None
    auto_analysis: bool | None = None
    stream_enabled: bool | None = None
    stream_rate_per_minute: float | None = None


def _view(monitor: Monitor, db: Database) -> dict[str, Any]:
    with db.session() as s:
        saved = app_settings.load(s)
    return {
        "settings": app_settings.current(monitor),
        "saved_keys": sorted(saved),
        "data_source": {
            "mode": "synthetic",
            "label": "Synthetic stream" if monitor.stream_enabled else "Synthetic dataset",
            "is_real_data": False,
            "real_bitcoin": REAL_BITCOIN,
        },
        "limits": {"interval_seconds": [1, 3600], "stream_rate_per_minute": [1, 600]},
        "secrets_note": "No credentials or API keys are stored or returned by this API.",
    }


@router.get("")
def get_settings(monitor: Monitor = Depends(get_monitor), db: Database = Depends(get_db)) -> dict[str, Any]:
    return _view(monitor, db)


@router.put("")
def update_settings(body: SettingsIn, monitor: Monitor = Depends(get_monitor), db: Database = Depends(get_db)) -> dict[str, Any]:
    """Apply and save monitor settings. An invalid value changes nothing."""
    values = body.model_dump(exclude_none=True)
    try:
        app_settings.apply(monitor, values)
    except MonitorConfigError as e:
        raise AppError(422, "invalid_configuration", str(e)) from e
    with db.transaction() as s:
        app_settings.save(s, values)
    return _view(monitor, db)
