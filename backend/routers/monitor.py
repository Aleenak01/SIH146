from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..deps import get_monitor
from ..errors import AppError
from ..services.monitor import Monitor, MonitorConfigError

router = APIRouter(prefix="/api/monitor", tags=["monitoring"])


class MonitorConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: float | None = None
    auto_analysis: bool | None = None
    stream_enabled: bool | None = None
    stream_rate_per_minute: float | None = None


@router.get("/status")
def status(monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    """Monitoring state: source label (always synthetic here), counts, last transaction/analysis, new leads."""
    return monitor.status()


@router.post("/start")
def start(monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    monitor.start()
    return monitor.status()


@router.post("/stop")
def stop(monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    monitor.stop()
    return monitor.status()


@router.patch("/config")
def configure(body: MonitorConfigIn, monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    """Change the tick interval, automatic analysis, and the synthetic stream (on/off, rate)."""
    try:
        monitor.configure(interval=body.interval_seconds, auto_analysis=body.auto_analysis,
                          stream_enabled=body.stream_enabled, stream_rate=body.stream_rate_per_minute)
    except MonitorConfigError as e:
        raise AppError(422, "invalid_configuration", str(e)) from e
    return monitor.status()


@router.post("/tick")
def tick_now(monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    """Process pending input now (inbox, stream, analysis) instead of waiting for the next automatic tick."""
    return {"result": monitor.tick(), "status": monitor.status()}


@router.post("/stream/scenario")
def inject_scenario(monitor: Monitor = Depends(get_monitor)) -> dict[str, Any]:
    """OPTIONAL demo scenario: a quiet synthetic wallet suddenly sends to many new wallets. Clearly labelled synthetic."""
    try:
        return monitor.inject_fan_out_scenario()
    except MonitorConfigError as e:
        raise AppError(422, "scenario_unavailable", str(e)) from e
