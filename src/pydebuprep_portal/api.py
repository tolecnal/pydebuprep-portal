"""The HTTP API under /api/v1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pydebuprep_portal.db import get_session
from pydebuprep_portal.ingest import IngestError, check_headers, read_body, store_report
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.tokens import TokenError, authenticate

router = APIRouter(prefix="/api/v1")


class Stored(BaseModel):
    id: int
    url: str


@router.post(
    "/reports",
    status_code=201,
    response_model=Stored,
    summary="Upload a report (upload token)",
    responses={
        200: {"model": Stored, "description": "Sent before; the stored report"},
        401: {"description": "No valid upload token"},
        413: {"description": "Larger than the portal accepts"},
        415: {"description": "Not application/json, or an encoding other than gzip"},
        422: {"description": "Not a pydebuprep report this portal reads"},
    },
)
async def upload(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Stored:
    """A pydebuprep JSON report, optionally gzip-compressed, with an upload token."""
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    try:
        token = await run_in_threadpool(authenticate, session, authorization, now)
    except TokenError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from None
    try:
        check_headers(request, settings.max_upload_bytes)
        body = await read_body(request, settings.max_upload_bytes)
        report, created = await run_in_threadpool(store_report, session, body, token, now)
    except IngestError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    response.status_code = 201 if created else 200
    return Stored(id=report.id, url=f"{settings.base_url}/reports/{report.id}")
