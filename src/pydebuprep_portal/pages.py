"""The portal's pages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydebuprep.checks import ALL_CHECKS
from pydebuprep.render import FORMATS
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydebuprep_portal.access import (
    Viewer,
    may_change_settings,
    may_delete_reports,
    may_manage_tokens,
    may_read_audit,
    may_read_tokens,
    may_set_retention,
    may_set_visibility,
)
from pydebuprep_portal.audit import actor_of, list_audit
from pydebuprep_portal.auth import page_viewer
from pydebuprep_portal.db import get_session
from pydebuprep_portal.mapping import (
    MappingError,
    map_host,
    queue,
    queue_visible,
    team_choices,
    unmap_host,
)
from pydebuprep_portal.models import Host, Team
from pydebuprep_portal.queries import (
    delete_report,
    get_report,
    host_timeline,
    list_hosts,
    list_reports,
    page_of,
    search_findings,
    visible_team_names,
)
from pydebuprep_portal.rendering import (
    MEDIA_TYPES,
    REPORT_CSP,
    download_name,
    render_stored,
)
from pydebuprep_portal.retention import describe, effective_retention
from pydebuprep_portal.security import DOCS_CSP, require_csrf
from pydebuprep_portal.settings_store import get_settings, update_settings
from pydebuprep_portal.teams import (
    create_team,
    list_teams,
    retention_text,
    set_retention,
    set_visibility,
    team_hosts,
    visible_team,
)
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
    team: str | None = None,
    flagged: str | None = None,
    before: int | None = None,
) -> HTMLResponse:
    team = team or None
    rows = list_reports(
        session, viewer, worst=worst, team=team, flagged=bool(flagged), before=before,
        limit=PAGE_SIZE + 1,
    )  # fmt: skip
    rows, next_before = page_of(rows, PAGE_SIZE)
    # Only the rows for htmx's filter requests; the whole page when htmx restores history.
    partial = request.headers.get("HX-Request") and not request.headers.get(
        "HX-History-Restore-Request"
    )
    template = "_report_rows.html" if partial else "reports.html"
    response = page(
        request, template, viewer, rows=rows, next_before=next_before, worst=worst, team=team,
        flagged=bool(flagged), teams=visible_team_names(session, viewer),
    )  # fmt: skip
    response.headers["Vary"] = "HX-Request, HX-History-Restore-Request"
    return response


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(
    request: Request, report_id: int, viewer: ViewerDep, session: SessionDep
) -> HTMLResponse:
    report = get_report(session, viewer, report_id)
    if report is None:
        return _not_found(request)
    may_delete = may_delete_reports(viewer, report.team.name if report.team else None)
    return page(
        request, "report.html", viewer, report=report, formats=FORMATS, may_delete=may_delete
    )


@router.post("/reports/{report_id}/delete", dependencies=[Depends(require_csrf)])
def delete_report_page(
    request: Request, report_id: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    try:
        deleted = delete_report(session, viewer, report_id, actor_of(viewer))
    except PermissionError:
        return _forbidden(request)
    if not deleted:
        return _not_found(request)
    return RedirectResponse("/", status_code=303)


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
    host = session.scalar(select(Host).where(Host.name == name.lower()))
    if host is None:  # pragma: no cover - it has reports
        return _not_found(request)
    choices = team_choices(session, viewer) if viewer.is_admin else []
    retention = describe(effective_retention(host, host.team, get_settings(session)))
    return page(
        request, "host.html", viewer, host=name.lower(), rows=rows,
        team=host.team.name if host.team else None, team_choices=choices, retention=retention,
        host_row=host,
    )  # fmt: skip


@router.post("/hosts/{name}/map", dependencies=[Depends(require_csrf)])
def map_host_page(
    request: Request, name: str, viewer: ViewerDep, session: SessionDep,
    team: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    return _map(request, session, viewer, name, team, f"/hosts/{name.lower()}")


@router.post("/hosts/{name}/unmap", dependencies=[Depends(require_csrf)])
def unmap_host_page(
    request: Request, name: str, viewer: ViewerDep, session: SessionDep
) -> Response:
    try:
        unmap_host(session, viewer, name)
    except MappingError as exc:
        return error_page(request, exc.status, "Not done", exc.detail)
    return RedirectResponse(f"/hosts/{name.lower()}", status_code=303)


def _map(
    request: Request, session: Session, viewer: Viewer, host: str, team: str, back: str
) -> Response:
    try:
        map_host(session, viewer, host, team)
    except MappingError as exc:
        return error_page(request, exc.status, "Not done", exc.detail)
    return RedirectResponse(back, status_code=303)


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    if not queue_visible(session, viewer):
        return _forbidden(request)
    return page(
        request, "queue.html", viewer, hosts=queue(session, viewer),
        team_choices=team_choices(session, viewer),
    )  # fmt: skip


@router.post("/queue/{host}/map", dependencies=[Depends(require_csrf)])
def map_queued_host(
    request: Request, host: str, viewer: ViewerDep, session: SessionDep,
    team: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    return _map(request, session, viewer, host, team, "/queue")


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


def _new_token_response(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store"  # the page shows a token, once
    return response


def _token_request(name: str, expires_days: str) -> tuple[str, datetime | None]:
    try:
        days = int(expires_days) if expires_days.strip() else None
    except ValueError:
        raise ValueError("expiry must be a number of days") from None
    return token_request(name, days, datetime.now(UTC))


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(request: Request, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    if not may_read_tokens(viewer, None):
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
    if not may_manage_tokens(viewer, "global", None):
        return _forbidden(request)
    try:
        clean, expires = _token_request(name, expires_days)
    except ValueError as exc:
        return _tokens_page(request, viewer, session, 422, error=str(exc), form_name=name)
    _, plain = create_token(
        session, kind="global", name=clean, actor=actor_of(viewer), expires_at=expires
    )
    return _new_token_response(
        _tokens_page(request, viewer, session, new_token=plain, new_name=clean)
    )


@router.post("/tokens/{token_id}/revoke", dependencies=[Depends(require_csrf)])
def revoke_token_page(
    request: Request, token_id: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    if not viewer.is_admin:
        return _forbidden(request)
    if not revoke_token(session, token_id, datetime.now(UTC), actor_of(viewer)):
        return error_page(request, 404, "Not found", "There is no such token.")
    return RedirectResponse("/tokens", status_code=303)


def _team_not_found(request: Request) -> HTMLResponse:
    return error_page(request, 404, "Not found", "There is no such team, or you can't see it.")


def _team_page(
    request: Request, viewer: Viewer, session: Session, team: Team, status_code: int = 200,
    **context: object,
) -> HTMLResponse:  # fmt: skip
    tokens = list_tokens(session, team.id) if may_read_tokens(viewer, team.name) else None
    return page(
        request, "team.html", viewer, status_code, team=team, tokens=tokens,
        may_manage=may_manage_tokens(viewer, "team", team.name),
        may_visibility=may_set_visibility(viewer, team.name), hosts=team_hosts(session, team),
        retention=retention_text(team, get_settings(session)), **context,
    )  # fmt: skip


def _teams_page(
    request: Request, viewer: Viewer, session: Session, status_code: int = 200, **context: object
) -> HTMLResponse:
    rows = list_teams(session, viewer)
    return page(request, "teams.html", viewer, status_code, teams=rows, **context)


@router.get("/teams", response_class=HTMLResponse)
def teams_page(request: Request, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    return _teams_page(request, viewer, session)


@router.post("/teams", dependencies=[Depends(require_csrf)])
def create_team_page(
    request: Request, viewer: ViewerDep, session: SessionDep, name: Annotated[str, Form()] = ""
) -> Response:
    if not viewer.is_admin:
        return _forbidden(request)
    try:
        team = create_team(session, actor_of(viewer), name)
    except ValueError as exc:
        return _teams_page(request, viewer, session, 422, error=str(exc), form_name=name)
    return RedirectResponse(f"/teams/{team.name}", status_code=303)


@router.post("/teams/{name}/visibility", dependencies=[Depends(require_csrf)])
def team_visibility_page(
    request: Request, name: str, viewer: ViewerDep, session: SessionDep,
    public: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    team = visible_team(session, viewer, name)
    if team is None:
        return _team_not_found(request)
    if not may_set_visibility(viewer, team.name):
        return _forbidden(request)
    set_visibility(session, actor_of(viewer), team, public == "1")
    return RedirectResponse(f"/teams/{team.name}", status_code=303)


@router.get("/teams/{name}", response_class=HTMLResponse)
def team_page(request: Request, name: str, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    team = visible_team(session, viewer, name)
    if team is None:
        return _team_not_found(request)
    return _team_page(request, viewer, session, team)


@router.post(
    "/teams/{name}/tokens", response_class=HTMLResponse, dependencies=[Depends(require_csrf)]
)
# token_name is a label, not a secret.
def create_team_token_page(  # nosec B107
    request: Request,
    name: str,
    viewer: ViewerDep,
    session: SessionDep,
    kind: Annotated[str, Form()] = "team",
    token_name: Annotated[str, Form(alias="name")] = "",
    host_name: Annotated[str, Form()] = "",
    expires_days: Annotated[str, Form()] = "",
) -> HTMLResponse:
    team = visible_team(session, viewer, name)
    if team is None:
        return _team_not_found(request)
    if not may_manage_tokens(viewer, "team", team.name):
        return _forbidden(request)
    try:
        if kind not in ("team", "host"):
            raise ValueError("a team's token is a team token or a host token")
        clean, expires = _token_request(token_name, expires_days)
        _, plain = create_token(
            session, kind=kind, name=clean, actor=actor_of(viewer), expires_at=expires,
            team_id=team.id, host_name=(host_name if kind == "host" else None),
        )  # fmt: skip
    except ValueError as exc:
        return _team_page(request, viewer, session, team, 422, error=str(exc))
    return _new_token_response(
        _team_page(request, viewer, session, team, new_token=plain, new_name=clean)
    )


@router.post("/teams/{name}/tokens/{token_id}/revoke", dependencies=[Depends(require_csrf)])
def revoke_team_token_page(
    request: Request, name: str, token_id: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    team = visible_team(session, viewer, name)
    if team is None:
        return _team_not_found(request)
    if not may_manage_tokens(viewer, "team", team.name):
        return _forbidden(request)
    if not revoke_token(session, token_id, datetime.now(UTC), actor_of(viewer), team.id):
        return error_page(request, 404, "Not found", "There is no such token in this team.")
    return RedirectResponse(f"/teams/{team.name}", status_code=303)


def _retention_form(mode: str, days: str) -> tuple[int | None, bool]:
    """(days, forever) from a retention form: inherit, N days, or keep forever."""
    if mode == "forever":
        return None, True
    if mode == "days":
        try:
            return int(days), False
        except ValueError:
            raise ValueError("retention is a number of days") from None
    return None, False


@router.post("/teams/{name}/retention", dependencies=[Depends(require_csrf)])
def team_retention_page(
    request: Request, name: str, viewer: ViewerDep, session: SessionDep,
    mode: Annotated[str, Form()] = "inherit", days: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    if not may_set_retention(viewer):
        return _forbidden(request)
    team = visible_team(session, viewer, name)
    if team is None:
        return _team_not_found(request)
    try:
        set_retention(session, actor_of(viewer), team=team, **_named(_retention_form(mode, days)))
    except ValueError as exc:
        return _team_page(request, viewer, session, team, 422, error=str(exc))
    return RedirectResponse(f"/teams/{team.name}", status_code=303)


@router.post("/hosts/{name}/retention", dependencies=[Depends(require_csrf)])
def host_retention_page(
    request: Request, name: str, viewer: ViewerDep, session: SessionDep,
    mode: Annotated[str, Form()] = "inherit", days: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    if not may_set_retention(viewer):
        return _forbidden(request)
    host = session.scalar(select(Host).where(Host.name == name.lower()))
    if host is None:
        return error_page(request, 404, "Not found", "There is no such host.")
    try:
        set_retention(session, actor_of(viewer), host=host, **_named(_retention_form(mode, days)))
    except ValueError as exc:
        return error_page(request, 422, "Not saved", str(exc))
    return RedirectResponse(f"/hosts/{host.name}", status_code=303)


def _named(value: tuple[int | None, bool]) -> dict[str, Any]:
    return {"days": value[0], "forever": value[1]}


def _settings_page(
    request: Request, viewer: Viewer, session: Session, status_code: int = 200, **context: object
) -> HTMLResponse:
    return page(
        request, "settings.html", viewer, status_code, settings=get_settings(session), **context
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, viewer: ViewerDep, session: SessionDep) -> HTMLResponse:
    if not viewer.reads_all:
        return _forbidden(request)
    return _settings_page(request, viewer, session)


@router.post("/settings", dependencies=[Depends(require_csrf)])
def change_settings_page(
    request: Request, viewer: ViewerDep, session: SessionDep,
    retention_days: Annotated[str, Form()] = "", keep_forever: Annotated[str, Form()] = "",
    only_admins_map: Annotated[str, Form()] = "", max_upload_mib: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    if not may_change_settings(viewer):
        return _forbidden(request)
    try:
        days = None if keep_forever else int(retention_days)
        mib = int(max_upload_mib)
        update_settings(
            session, actor_of(viewer), retention_days=days, only_admins_map=bool(only_admins_map),
            max_upload_bytes=mib * 1024 * 1024,
        )  # fmt: skip
    except ValueError as exc:
        message = str(exc) if "days" in str(exc) or "limit" in str(exc) else "use whole numbers"
        return _settings_page(request, viewer, session, 422, error=message)
    return RedirectResponse("/settings", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request, viewer: ViewerDep, session: SessionDep,
    action: str = "", before: int | None = None,
) -> HTMLResponse:  # fmt: skip
    if not may_read_audit(viewer):
        return _forbidden(request)
    entries, next_before = page_of(
        list_audit(session, action=action or None, before=before, limit=PAGE_SIZE + 1), PAGE_SIZE
    )
    return page(
        request, "audit.html", viewer, entries=entries, next_before=next_before, action=action
    )


@router.get("/docs", response_class=HTMLResponse)
def docs(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request, "docs.html", {})
    response.headers["Content-Security-Policy"] = DOCS_CSP
    return response
