from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from ..config import Settings
from ..database import Database
from ..deps import get_db, get_settings
from ..errors import AppError
from ..ingestion.base import DatasetError
from ..schemas import ImportRequest, ImportResult
from ..services.importer import ImportConflict, import_synthetic_csv

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/synthetic-csv", response_model=ImportResult)
def import_csv(
    request: ImportRequest = Body(default_factory=ImportRequest),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Import the existing synthetic dataset (dataset/synthetic_bitcoin_transactions.csv). Safe to repeat.
    Use {"replace": true} after regenerating the dataset; that removes all existing synthetic data first.
    """
    try:
        return import_synthetic_csv(db, settings.dataset_csv, replace=request.replace)
    except DatasetError as e:
        raise AppError(422, "invalid_dataset", str(e)) from e
    except ImportConflict as e:
        raise AppError(409, "import_conflict", str(e)) from e
