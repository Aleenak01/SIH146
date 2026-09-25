from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..errors import AppError
from ..schemas import SearchOut
from ..services import search as search_service

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchOut)
def search(
    q: Annotated[str, Query(max_length=128, description="Wallet address, transaction ID, cluster ID, IP address or observation ID, device ID, or case ID/title")],
    session: Session = Depends(get_session),
    types: Annotated[str | None, Query(description="Comma list of: " + ", ".join(search_service.CATEGORIES))] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum hits per category")] = 10,
):
    """Search everything at once; results are grouped by category with the full match count of each."""
    if len(q.strip()) < search_service.MIN_QUERY:
        raise AppError(422, "validation_error", f"Type at least {search_service.MIN_QUERY} characters to search.")
    chosen = None
    if types and types.strip():
        chosen = {t.strip() for t in types.split(",") if t.strip()}
        bad = chosen - set(search_service.CATEGORIES)
        if bad:
            raise AppError(422, "validation_error", f"Unknown types: {', '.join(sorted(bad))}. Allowed: {', '.join(search_service.CATEGORIES)}.")
    return search_service.search(session, q, chosen, limit)
