"""The portal's pages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydebuprep.checks import ALL_CHECKS
from pydebuprep.render import FORMATS
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.auth import page_viewer
from pydebuprep_portal.db import get_session
from pydebuprep_portal.queries import (
    get_report,
    host_timeline,
    list_hosts,
    list_reports,
    page_of,
    search_findings,
)
from pydebuprep_portal.rendering import (
    MEDIA_TYPES,
    REPORT_CSP,
    download_name,
    render_stored,
)
from pydebuprep_portal.security import DOCS_CSP, require_csrf
from pydebuprep_portal.templating import error_page, page, templates
from pydebuprep_portal.tokens import create_token, list_tokens, revoke_token, token_request

router = APIRouter(include_in_schema=False)

PAGE_SIZE = 50
Worst = Literal["critical", "warning", "info", "ok"]
ViewerDep = Annotated[Viewer, Depends(page_viewer)]
SessionDep = Annotated[Session, Depends(get_session)]


def _not_found(request: Request) -> HTMLResponse:
    return error_page(request, 404, "Not found", "There is no such report, or you can't see it.")


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    viewer: ViewerDep,
    session: SessionDep,
    worst: Worst | None = None,
    before: int | None = None,
) -> HTMLResponse:
    rows = list_reports(session, viewer, worst=worst, before=before, limit=PAGE_SIZE + 1)
    rows, next_before = page_of(rows, PAGE_SIZE)
    # Only the rows for htmx's filter requests; the whole page when htmx restores history.
    partial = request.headers.get("HX-Request") and not request.headers.get(
        "HX-History-Restore-Request"
    )
    template = "_report_rows.html" if partial else "reports.html"
    response = page(request, template, viewer, rows=rows, next_before=next_before, worst=worst)
    response.headers["Vary"] = "HX-Request, HX-History-Restore-Request"
    return response


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(
    request: Request, report_id: int, viewer: ViewerDep, session: SessionDep
) -> HTMLResponse:
    report = get_report(session, viewer, report_id)
    if report is None:
        return _not_found(request)
    return page(request, "report.html", viewer, report=report, formats=FORMATS)


@router.get("/reports/{report_id}/html")
def report_html(
    request: Request, report_id: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    report = get_report(session, viewer, report_id)
    if report is None:
        return _not_found(request)
    return Response(
        render_stored(report.body, "html"),
        media_type=MEDIA_TYPES["html"],
        headers={"Content-Security-Policy": REPORT_CSP},
    )


@router.get("/reports/{report_id}/download/{fmt}")
def report_download(
    request: Request, report_id: int, fmt: str, viewer: ViewerDep, session: SessionDep
) -> Response:
    report = get_report(session, viewer, report_id)
    if report is None or fmt not in FORMATS:
        return _not_found(request)
    name = download_name(report.host.name, report.generated_at, fmt)
    return Response(
        render_stored(report.body, fmt),
        media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/hosts", response_class=HTMLResponse)
def hosts_page(
    request: Request,
    viewer: ViewerDep,
    session: SessionDep,
    name: str | None = None,
    worse: str | None = None,
) -> HTMLResponse:
    rows = list_hosts(session, viewer, name=name, worse_only=bool(worse))
    return page(request, "hosts.html", viewer, rows=rows, name_filter=name or "", worse=bool(worse))


@router.get("/hosts/{name}", response_class=HTMLResponse)
def host_page(request: Request, name: str, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    rows = host_timeline(session, viewer, name)
    if rows is None:
        return error_page(request, 404, "Not found", "There is no such host, or you can't see it.")
    return page(request, "host.html", viewer, host=name.lower(), rows=rows)


SEARCH_FIELDS = ("host", "check", "severity", "words")
SEVERITIES = ("critical", "warning", "info")


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    viewer: ViewerDep,
    session: SessionDep,
    host: str = "",
    check: str = "",
    severity: str = "",
    words: str = "",
    before: int | None = None,
) -> HTMLResponse:
    form = {"host": host, "check": check, "severity": severity, "words": words}
    error = None
    hits: list[object] = []
    next_before = None
    if severity and severity not in SEVERITIES:
        error = f"Unknown severity {severity!r}: choose critical, warning or info."
    elif any(form.values()):
        found = search_findings(
            session, viewer, host=host or None, check=check or None, severity=severity or None,
            words=words or None, before=before, limit=PAGE_SIZE + 1,
        )  # fmt: skip
        page_hits, next_before = page_of(found, PAGE_SIZE)
        hits = list(page_hits)
    checks = sorted(cls.id for cls in ALL_CHECKS)
    return page(
        request, "search.html", viewer, form=form, hits=hits, next_before=next_before,
        error=error, checks=checks, severities=SEVERITIES, searched=any(form.values()),
    )  # fmt: skip


def _forbidden(request: Request) -> HTMLResponse:
    return error_page(request, 403, "Not allowed", "Your roles don't allow this.")


def _tokens_page(
    request: Request, viewer: Viewer, session: Session, status_code: int = 200, **context: object
) -> HTMLResponse:
    return page(request, "tokens.html", viewer, status_code, tokens=list_tokens(session), **context)


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(request: Request, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    if not viewer.reads_all:
        return _forbidden(request)
    return _tokens_page(request, viewer, session)


@router.post("/tokens", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def create_token_page(
    request: Request,
    viewer: ViewerDep,
    session: SessionDep,
    name: Annotated[str, Form()] = "",
    expires_days: Annotated[str, Form()] = "",
) -> HTMLResponse:
    if not viewer.is_admin:
        return _forbidden(request)
    try:
        days = int(expires_days) if expires_days.strip() else None
        clean, expires = token_request(name, days, datetime.now(UTC))
    except ValueError as exc:
        message = str(exc) if "token" in str(exc) else "expiry must be a number of days"
        return _tokens_page(request, viewer, session, 422, error=message, form_name=name)
    _, plain = create_token(
        session,
        kind="global",
        name=clean,
        created_by=f"{viewer.name} <{viewer.subject}>",
        expires_at=expires,
    )
    response = _tokens_page(request, viewer, session, new_token=plain, new_name=clean)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/tokens/{token_id}/revoke", dependencies=[Depends(require_csrf)])
def revoke_token_page(
    request: Request, token_id: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    if not viewer.is_admin:
        return _forbidden(request)
    if not revoke_token(session, token_id, datetime.now(UTC)):
        return error_page(request, 404, "Not found", "There is no such token.")
    return RedirectResponse("/tokens", status_code=303)


@router.get("/docs", response_class=HTMLResponse)
def docs(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request, "docs.html", {})
    response.headers["Content-Security-Policy"] = DOCS_CSP
    return response
