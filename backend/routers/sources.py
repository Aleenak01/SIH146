from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import Database
from ..deps import get_db, get_session, get_settings
from ..errors import AppError
from ..ingestion.real_bitcoin import SourceNotConfigured, SourceUnavailable, urllib_transport
from ..services import real_source

router = APIRouter(prefix="/api/sources", tags=["data sources"])


class RealFetchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: Annotated[int, Field(ge=1, le=50, description="How many of the newest blocks to read")] = 1
    heights: Annotated[list[int] | None, Field(max_length=50, description="Explicit block heights instead of the newest blocks")] = None
    max_transactions_per_block: Annotated[int | None, Field(ge=1, le=100_000)] = None


def _state(request: Request) -> real_source.RealSourceState:
    return request.app.state.real_source


@router.get("")
def list_sources(request: Request, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Every data source and its state. Synthetic sources are always available; the real one is optional and off by default."""
    real = real_source.status(settings, session, _state(request))
    return {
        "sources": [
            {"source": "synthetic", "kind": "synthetic_csv", "label": "Synthetic dataset (CSV)", "status": "available", "is_real_data": False},
            {"source": "synthetic", "kind": "synthetic_stream", "label": "Synthetic stream", "status": "available", "is_real_data": False},
            {"source": "real_bitcoin", "kind": "esplora", "label": "Real Bitcoin (Esplora API)", "status": real["status"], "is_real_data": True,
             "detail": real["label"]},
        ],
    }


@router.get("/real-bitcoin")
def real_bitcoin(request: Request, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Configuration (no secrets), stored real data, and which model features can be computed from real data."""
    return real_source.status(settings, session, _state(request))


@router.post("/real-bitcoin/fetch")
def fetch_real_bitcoin(
    request: Request,
    body: RealFetchIn = Body(default_factory=RealFetchIn),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Read recent confirmed blocks from the configured Esplora API and store them as source = real_bitcoin. Only works when
    the source is enabled in the environment. All-or-nothing: if the API fails, nothing is stored and nothing is invented.
    """
    transport = getattr(request.app.state, "real_bitcoin_transport", None) or urllib_transport
    try:
        return real_source.fetch_and_ingest(db, settings, _state(request), blocks=body.blocks, heights=body.heights,
                                            max_tx_per_block=body.max_transactions_per_block, transport=transport)
    except SourceNotConfigured as e:
        raise AppError(409, "source_not_enabled", str(e)) from e
    except SourceUnavailable as e:
        raise AppError(502, "source_unavailable", str(e)) from e
    except ValueError as e:
        raise AppError(422, "invalid_request", str(e)) from e
    except real_source.FetchBusy as e:
        raise AppError(409, "fetch_in_progress", str(e)) from e
