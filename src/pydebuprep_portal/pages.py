"""The portal's pages."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydebuprep.render import FORMATS
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.auth import page_viewer
from pydebuprep_portal.db import get_session
from pydebuprep_portal.queries import get_report, list_reports, page_of
from pydebuprep_portal.rendering import (
    MEDIA_TYPES,
    REPORT_CSP,
    download_name,
    render_stored,
)
from pydebuprep_portal.security import DOCS_CSP
from pydebuprep_portal.templating import error_page, page, templates

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
    template = "_report_rows.html" if request.headers.get("HX-Request") else "reports.html"
    return page(request, template, viewer, rows=rows, next_before=next_before, worst=worst)


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


@router.get("/docs", response_class=HTMLResponse)
def docs(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request, "docs.html", {})
    response.headers["Content-Security-Policy"] = DOCS_CSP
    return response
