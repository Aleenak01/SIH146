"""
Persisted local settings for the monitor (on/off, interval, automatic analysis, synthetic stream).

Stored in the `app_settings` table so a restart keeps what the investigator chose. Only small non-secret values live here:
no credentials, tokens or keys are ever stored or returned by the API.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting
from .monitor import Monitor, MonitorConfigError

KEYS = ("monitor_enabled", "interval_seconds", "auto_analysis", "stream_enabled", "stream_rate_per_minute")


def load(session: Session) -> dict[str, Any]:
    return {r.key: r.value for r in session.scalars(select(AppSetting)) if r.key in KEYS}


def save(session: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in KEYS:
            raise KeyError(key)
        row = session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value


def current(monitor: Monitor) -> dict[str, Any]:
    """What is in effect right now."""
    return {"monitor_enabled": monitor.running, "interval_seconds": monitor.interval, "auto_analysis": monitor.auto_analysis,
            "stream_enabled": monitor.stream_enabled, "stream_rate_per_minute": monitor.stream_rate}


def apply(monitor: Monitor, values: dict[str, Any]) -> None:
    """Apply settings to the monitor. Raises MonitorConfigError for an invalid value (nothing is half-applied)."""
    monitor.configure(interval=values.get("interval_seconds"), auto_analysis=values.get("auto_analysis"),
                      stream_enabled=values.get("stream_enabled"), stream_rate=values.get("stream_rate_per_minute"))
    if "monitor_enabled" in values:
        monitor.start() if values["monitor_enabled"] else monitor.stop()


def apply_persisted(db, monitor: Monitor, autostart: bool) -> None:
    """At startup: persisted choices win over the environment default. A bad stored value must not stop the server."""
    with db.session() as s:
        values = load(s)
    values.setdefault("monitor_enabled", autostart)
    try:
        apply(monitor, values)
    except MonitorConfigError as e:
        monitor.last_error = f"Saved setting not applied: {e}"
        if values["monitor_enabled"]:
            monitor.start()
