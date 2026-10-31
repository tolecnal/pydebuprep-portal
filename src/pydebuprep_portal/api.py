"""The HTTP API under /api/v1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pydebuprep.render import FORMATS
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.auth import api_viewer
from pydebuprep_portal.db import get_session
from pydebuprep_portal.ingest import IngestError, check_headers, read_body, store_report
from pydebuprep_portal.queries import (
    ReportRow,
    get_report,
    host_timeline,
    list_hosts,
    list_reports,
    page_of,
    report_row,
    search_findings,
)
from pydebuprep_portal.rendering import MEDIA_TYPES, render_stored
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.tokens import (
    MAX_EXPIRES_DAYS,
    MAX_NAME,
    TokenError,
    authenticate,
    create_token,
    list_tokens,
    revoke_token,
    token_request,
)

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


class WhoAmI(BaseModel):
    subject: str
    name: str
    roles: list[str]


@router.get("/whoami", summary="Who the access token belongs to")
def whoami(viewer: Annotated[Viewer, Depends(api_viewer)]) -> WhoAmI:
    return WhoAmI(subject=viewer.subject, name=viewer.name, roles=sorted(viewer.roles))


PAGE_SIZE = 50
Worst = Literal["critical", "warning", "info", "ok"]
ViewerDep = Annotated[Viewer, Depends(api_viewer)]
SessionDep = Annotated[Session, Depends(get_session)]
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such report, or not visible to you"}
}


class ReportSummary(BaseModel):
    id: int
    host: str
    generated_at: datetime
    received_at: datetime
    worst: str
    critical: int
    warning: int
    info: int
    error: int
    os: str
    kernel: str
    config_stale: bool

    @classmethod
    def of(cls, row: ReportRow) -> ReportSummary:
        return cls(**row.__dict__)


class ReportList(BaseModel):
    reports: list[ReportSummary]
    next_before: int | None


class ReportDetail(ReportSummary):
    os_id: str
    os_version: str
    codename: str
    tool_version: str
    token: str
    formats: list[str]


@router.get("/reports", summary="Newest reports first")
def reports(
    viewer: ViewerDep,
    session: SessionDep,
    worst: Worst | None = None,
    host: str | None = None,
    before: int | None = None,
) -> ReportList:
    """Fifty at a time; pass `next_before` as `before` for the next page."""
    rows = list_reports(session, viewer, worst=worst, host=host, before=before, limit=PAGE_SIZE + 1)
    rows, next_before = page_of(rows, PAGE_SIZE)
    return ReportList(reports=[ReportSummary.of(r) for r in rows], next_before=next_before)


@router.get("/reports/{report_id}", summary="A report's details", responses=NOT_FOUND)
def report(report_id: int, viewer: ViewerDep, session: SessionDep) -> ReportDetail:
    found = get_report(session, viewer, report_id)
    if found is None:
        raise HTTPException(404, "no such report")
    return ReportDetail(
        **report_row(found).__dict__,
        os_id=found.os_id,
        os_version=found.os_version,
        codename=found.codename,
        tool_version=found.tool_version,
        token=found.token.name,
        formats=list(FORMATS),
    )


@router.get(
    "/reports/{report_id}/{fmt}",
    summary="A report rendered by pydebuprep",
    response_class=Response,
    responses=NOT_FOUND,
)
def report_rendered(report_id: int, fmt: str, viewer: ViewerDep, session: SessionDep) -> Response:
    """`fmt` is one of text, json, yaml, html, markdown, jira, confluence."""
    found = get_report(session, viewer, report_id)
    if found is None or fmt not in FORMATS:
        raise HTTPException(404, "no such report or format")
    return Response(render_stored(found.body, fmt), media_type=MEDIA_TYPES[fmt])


class HostSummary(BaseModel):
    name: str
    last_report_id: int
    last_generated_at: datetime
    worst: str
    previous_worst: str | None
    worse: bool


class HostList(BaseModel):
    hosts: list[HostSummary]


class Timeline(BaseModel):
    host: str
    reports: list[ReportSummary]


@router.get("/hosts", summary="Hosts and their newest report")
def hosts(
    viewer: ViewerDep, session: SessionDep, name: str | None = None, worse_only: bool = False
) -> HostList:
    """`worse` is true when the newest report is worse than the one before it."""
    rows = list_hosts(session, viewer, name=name, worse_only=worse_only)
    return HostList(hosts=[HostSummary(**r.__dict__) for r in rows])


@router.get(
    "/hosts/{name}/timeline",
    summary="A host's reports, oldest first",
    responses={404: {"description": "No such host, or not visible to you"}},
)
def timeline(name: str, viewer: ViewerDep, session: SessionDep) -> Timeline:
    rows = host_timeline(session, viewer, name)
    if rows is None:
        raise HTTPException(404, "no such host")
    return Timeline(host=name.lower(), reports=[ReportSummary.of(r) for r in rows])


class FindingOut(BaseModel):
    id: int
    report_id: int
    host: str
    generated_at: datetime
    check_id: str
    severity: str
    title: str


class FindingList(BaseModel):
    findings: list[FindingOut]
    next_before: int | None


@router.get("/search/findings", summary="Search findings, newest first")
def findings(
    viewer: ViewerDep,
    session: SessionDep,
    host: str | None = None,
    check: str | None = None,
    severity: Literal["critical", "warning", "info"] | None = None,
    words: str | None = None,
    before: int | None = None,
) -> FindingList:
    """`host` matches part of a name; every one of `words` must appear in the title."""
    hits = search_findings(
        session, viewer, host=host, check=check, severity=severity, words=words,
        before=before, limit=PAGE_SIZE + 1,
    )  # fmt: skip
    hits, next_before = page_of(hits, PAGE_SIZE)
    return FindingList(findings=[FindingOut(**h.__dict__) for h in hits], next_before=next_before)


class TokenOut(BaseModel):
    id: int
    kind: str
    name: str
    public_id: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class TokenList(BaseModel):
    tokens: list[TokenOut]


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    expires_days: int | None = Field(None, ge=1, le=MAX_EXPIRES_DAYS)


class NewToken(BaseModel):
    id: int
    token: str = Field(description="Shown this once; the portal keeps only its hash")


FORBIDDEN = HTTPException(403, "your roles don't allow this")
TOKEN_FIELDS = tuple(TokenOut.model_fields)


@router.get("/tokens", summary="Upload tokens (admins and auditors)")
def tokens(viewer: ViewerDep, session: SessionDep) -> TokenList:
    if not viewer.reads_all:
        raise FORBIDDEN
    return TokenList(
        tokens=[TokenOut(**{f: getattr(t, f) for f in TOKEN_FIELDS}) for t in list_tokens(session)]
    )


@router.post("/tokens", status_code=201, summary="Create a global upload token (admins)")
def new_token(request: TokenRequest, viewer: ViewerDep, session: SessionDep) -> NewToken:
    if not viewer.is_admin:
        raise FORBIDDEN
    try:
        name, expires = token_request(request.name, request.expires_days, datetime.now(UTC))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    token, plain = create_token(
        session,
        kind="global",
        name=name,
        created_by=f"{viewer.name} <{viewer.subject}>",
        expires_at=expires,
    )
    return NewToken(id=token.id, token=plain)


@router.delete(
    "/tokens/{token_id}",
    status_code=204,
    summary="Revoke an upload token (admins)",
    responses={404: {"description": "No such token"}},
)
def delete_token(token_id: int, viewer: ViewerDep, session: SessionDep) -> Response:
    if not viewer.is_admin:
        raise FORBIDDEN
    if not revoke_token(session, token_id, datetime.now(UTC)):
        raise HTTPException(404, "no such token")
    return Response(status_code=204)
