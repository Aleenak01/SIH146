from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis.clustering import refresh_clusters
from ..config import Settings
from ..database import Database
from ..deps import get_db, get_session, get_settings
from ..errors import AppError
from ..models import NetworkObservation, Transaction
from ..schemas import EntityDetail, NetworkImportOut, NetworkImportRequest, ObservationOut, ObservationPage
from ..services import network as network_service

router = APIRouter(prefix="/api", tags=["synthetic network observations"])


def _obs_out(o: NetworkObservation) -> ObservationOut:
    out = ObservationOut.model_validate(o)
    out.observed_party = {"S": "sender", "R": "receiver"}.get(o.observation_id[-1:]) if o.observation_id[-2:-1] == "-" else None
    return out


@router.get("/network/observations", response_model=ObservationPage)
def list_observations(
    session: Session = Depends(get_session),
    wallet: Annotated[str | None, Query(max_length=128)] = None,
    transaction_id: Annotated[str | None, Query(max_length=80)] = None,
    ip: Annotated[str | None, Query(max_length=64)] = None,
    device: Annotated[str | None, Query(max_length=64)] = None,
    session_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """SYNTHETIC network observations (IP / device / session) attached to transactions. Demo data, not blockchain data."""
    N = NetworkObservation
    clauses = [c for c in (
        N.wallet_address == wallet if wallet else None, N.transaction_id == transaction_id if transaction_id else None,
        N.ip_address == ip if ip else None, N.device_id == device if device else None, N.session_id == session_id if session_id else None) if c is not None]
    total = session.scalar(select(func.count()).select_from(N).where(*clauses)) or 0
    rows = session.scalars(select(N).where(*clauses).order_by(N.observed_at.desc(), N.observation_id).limit(limit).offset(offset))
    return ObservationPage(total=total, limit=limit, offset=offset, items=[_obs_out(o) for o in rows])


@router.get("/network/observations/{observation_id}", response_model=ObservationOut)
def get_observation(observation_id: str, session: Session = Depends(get_session)):
    o = session.get(NetworkObservation, observation_id)
    if o is None:
        raise AppError(404, "not_found", f"No observation {observation_id!r}.")
    return _obs_out(o)


@router.get("/network/entities/{entity_type}/{entity_id}", response_model=EntityDetail)
def get_entity(entity_type: Literal["ip", "device", "session"], entity_id: str, session: Session = Depends(get_session)):
    """A synthetic IP, device or session: how often it was observed, by which wallets, and what it is linked to."""
    N = NetworkObservation
    column = {"ip": N.ip_address, "device": N.device_id, "session": N.session_id}[entity_type]
    rows = list(session.scalars(select(N).where(column == entity_id)))
    if not rows:
        raise AppError(404, "not_found", f"No synthetic {entity_type} {entity_id!r}.")
    related = {
        "devices": sorted({o.device_id for o in rows if o.device_id} - ({entity_id} if entity_type == "device" else set())),
        "ips": sorted({o.ip_address for o in rows if o.ip_address} - ({entity_id} if entity_type == "ip" else set())),
        "sessions": sorted({o.session_id for o in rows if o.session_id} - ({entity_id} if entity_type == "session" else set()))[:50],
    }
    times = [o.observed_at for o in rows if o.observed_at]
    return EntityDetail(
        entity_type=entity_type, entity_id=entity_id, observation_count=len(rows), wallets=sorted({o.wallet_address for o in rows}),
        transactions=len({o.transaction_id for o in rows if o.transaction_id}), related=related,
        first_observed_at=min(times) if times else None, last_observed_at=max(times) if times else None)


@router.post("/import/synthetic-network", response_model=NetworkImportOut)
def import_network(
    request: NetworkImportRequest = Body(default_factory=NetworkImportRequest),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Import the SYNTHETIC network-observation dataset (data/synthetic_network_observations.csv), generating it from the
    imported transactions first if it does not exist (or when `generate` is true). Then refreshes the clusters.
    """
    with db.session() as s:
        n_tx = s.scalar(select(func.count()).select_from(Transaction).where(Transaction.source == "synthetic")) or 0
    if n_tx == 0:
        raise AppError(422, "no_transactions", "Import the synthetic transactions first (POST /api/import/synthetic-csv).")
    generated = False
    if request.generate or not settings.network_csv.is_file():
        network_service.generate_csv(db, settings.network_csv, settings.network_seed)
        generated = True
    try:
        r = network_service.import_csv(db, settings.network_csv, replace=request.replace)
    except ValueError as e:
        raise AppError(422, "invalid_dataset", str(e)) from e
    clusters = refresh_clusters(db, "synthetic").total
    return NetworkImportOut(file=r.file, rows_in_file=r.rows_in_file, inserted=r.inserted, skipped_existing=r.skipped_existing,
                            rejected=r.rejected[:20], observations_total=r.observations_total, clusters_total=clusters, generated=generated)
