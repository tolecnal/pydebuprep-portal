"""The HTTP API under /api/v1."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pydebuprep.render import FORMATS
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pydebuprep_portal.access import (
    Viewer,
    may_change_settings,
    may_manage_tokens,
    may_read_audit,
    may_read_configs,
    may_read_tokens,
    may_set_retention,
    may_set_visibility,
)
from pydebuprep_portal.audit import actor_of, list_audit
from pydebuprep_portal.auth import api_viewer
from pydebuprep_portal.configs import (
    ConfigError,
    create_config,
    etag,
    get_config,
    get_version,
    list_configs,
    newest,
    render_served,
    save_config,
    served_problems,
    served_url,
)
from pydebuprep_portal.db import get_session
from pydebuprep_portal.ingest import IngestError, check_headers, read_body, store_report
from pydebuprep_portal.mapping import MappingError, map_host, queue, queue_visible, unmap_host
from pydebuprep_portal.models import Config, Host, Team, Token
from pydebuprep_portal.queries import (
    ReportRow,
    delete_report,
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
from pydebuprep_portal.settings_store import get_settings, update_settings
from pydebuprep_portal.teams import (
    create_team,
    get_team,
    list_teams,
    set_retention,
    set_visibility,
    visible_team,
)
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

log = logging.getLogger(__name__)
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
    token_id = token.id
    limit = (await run_in_threadpool(get_settings, session)).max_upload_bytes
    # Hand the connection back while the body arrives, however slowly it comes.
    await run_in_threadpool(session.rollback)
    try:
        check_headers(request, limit)
        body = await read_body(request, limit)
        report, created = await run_in_threadpool(store_report, session, body, token_id, now)
    except IngestError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    response.status_code = 201 if created else 200
    return Stored(id=report.id, url=f"{settings.base_url}/reports/{report.id}")


class WhoAmI(BaseModel):
    subject: str
    name: str
    roles: list[str]
    teams: list[str]
    leads: list[str]


@router.get("/whoami", summary="Who the access token belongs to")
def whoami(viewer: Annotated[Viewer, Depends(api_viewer)]) -> WhoAmI:
    return WhoAmI(
        subject=viewer.subject,
        name=viewer.name,
        roles=sorted(viewer.roles),
        teams=sorted(viewer.teams),
        leads=sorted(viewer.leads),
    )


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
    team: str | None = Field(description="None: the unassigned queue")
    flagged_team: str | None = Field(description="Sent with this other team's token")

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
    team: str | None = None,
    flagged: bool = False,
    before: int | None = None,
) -> ReportList:
    """Fifty at a time; pass `next_before` as `before` for the next page. `team` is a team's
    name or `unassigned`; `flagged` keeps reports sent with another team's token."""
    rows = list_reports(
        session, viewer, worst=worst, host=host, team=team, flagged=flagged, before=before,
        limit=PAGE_SIZE + 1,
    )  # fmt: skip
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
    team: str | None
    host_name: str | None
    created_by: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None

    @classmethod
    def of(cls, token: Token) -> TokenOut:
        fields = {f: getattr(token, f) for f in cls.model_fields if f != "team"}
        return cls(**fields, team=token.team.name if token.team else None)


class TokenList(BaseModel):
    tokens: list[TokenOut]


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    expires_days: int | None = Field(None, ge=1, le=MAX_EXPIRES_DAYS)


class TeamTokenRequest(TokenRequest):
    kind: Literal["team", "host"]
    host_name: str | None = Field(None, description="For a host token: the one host it is for")


class NewToken(BaseModel):
    id: int
    token: str = Field(description="Shown this once; the portal keeps only its hash")


FORBIDDEN = HTTPException(403, "your roles don't allow this")
NO_TEAM = HTTPException(404, "no such team")
TEAM_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such team, or not visible to you"}
}


def _new_token(
    session: Session, viewer: Viewer, request: TokenRequest, kind: str,
    team_id: int | None = None, host_name: str | None = None,
) -> NewToken:  # fmt: skip
    try:
        name, expires = token_request(request.name, request.expires_days, datetime.now(UTC))
        token, plain = create_token(
            session, kind=kind, name=name, actor=actor_of(viewer), expires_at=expires,
            team_id=team_id, host_name=host_name,
        )  # fmt: skip
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return NewToken(id=token.id, token=plain)


@router.get("/tokens", summary="Every upload token (admins and auditors)")
def tokens(viewer: ViewerDep, session: SessionDep) -> TokenList:
    if not may_read_tokens(viewer, None):
        raise FORBIDDEN
    return TokenList(tokens=[TokenOut.of(t) for t in list_tokens(session)])


@router.post("/tokens", status_code=201, summary="Create a global upload token (admins)")
def new_token(request: TokenRequest, viewer: ViewerDep, session: SessionDep) -> NewToken:
    if not may_manage_tokens(viewer, "global", None):
        raise FORBIDDEN
    return _new_token(session, viewer, request, "global")


@router.delete(
    "/tokens/{token_id}",
    status_code=204,
    summary="Revoke any upload token (admins)",
    responses={404: {"description": "No such token"}},
)
def delete_token(token_id: int, viewer: ViewerDep, session: SessionDep) -> Response:
    if not viewer.is_admin:
        raise FORBIDDEN
    if not revoke_token(session, token_id, datetime.now(UTC), actor_of(viewer)):
        raise HTTPException(404, "no such token")
    return Response(status_code=204)


def _team(session: Session, viewer: Viewer, name: str) -> Team:
    team = visible_team(session, viewer, name)
    if team is None:
        raise NO_TEAM
    return team


@router.get("/teams/{name}/tokens", summary="A team's tokens", responses=TEAM_NOT_FOUND)
def team_tokens(name: str, viewer: ViewerDep, session: SessionDep) -> TokenList:
    """Admins, auditors and the team's leads."""
    team = _team(session, viewer, name)
    if not may_read_tokens(viewer, team.name):
        raise FORBIDDEN
    return TokenList(tokens=[TokenOut.of(t) for t in list_tokens(session, team.id)])


@router.post(
    "/teams/{name}/tokens",
    status_code=201,
    summary="Create a team or host token (admins, the team's leads)",
    responses=TEAM_NOT_FOUND,
)
def new_team_token(
    name: str, request: TeamTokenRequest, viewer: ViewerDep, session: SessionDep
) -> NewToken:
    team = _team(session, viewer, name)
    if not may_manage_tokens(viewer, request.kind, team.name):
        raise FORBIDDEN
    return _new_token(session, viewer, request, request.kind, team.id, request.host_name)


@router.delete(
    "/teams/{name}/tokens/{token_id}",
    status_code=204,
    summary="Revoke a team's token (admins, the team's leads)",
    responses=TEAM_NOT_FOUND,
)
def delete_team_token(name: str, token_id: int, viewer: ViewerDep, session: SessionDep) -> Response:
    team = _team(session, viewer, name)
    if not may_manage_tokens(viewer, "team", team.name):
        raise FORBIDDEN
    if not revoke_token(session, token_id, datetime.now(UTC), actor_of(viewer), team.id):
        raise HTTPException(404, "no such token in this team")
    return Response(status_code=204)


class QueuedHostOut(BaseModel):
    name: str
    os: str
    first_seen: datetime
    last_seen: datetime
    reports: int | None = Field(description="None for leads, who see no report content")
    worst: str | None


class QueueOut(BaseModel):
    hosts: list[QueuedHostOut]


class MapRequest(BaseModel):
    team: str


class Mapped(BaseModel):
    host: str
    team: str | None
    reports_moved: int


def _mapping_error(exc: MappingError) -> HTTPException:
    return HTTPException(exc.status, exc.detail)


@router.get("/queue", summary="Unmapped hosts (admins, auditors, leads)")
def unassigned_queue(viewer: ViewerDep, session: SessionDep) -> QueueOut:
    """Leads see the host names, to map them; reading the queued reports needs an admin
    or auditor."""
    if not queue_visible(session, viewer):
        raise FORBIDDEN
    return QueueOut(hosts=[QueuedHostOut(**h.__dict__) for h in queue(session, viewer)])


@router.put("/hosts/{name}/team", summary="Map a host to a team, moving its reports")
def put_host_team(name: str, request: MapRequest, viewer: ViewerDep, session: SessionDep) -> Mapped:
    try:
        moved = map_host(session, viewer, name, request.team)
    except MappingError as exc:
        raise _mapping_error(exc) from None
    return Mapped(host=name.lower(), team=request.team, reports_moved=moved)


@router.delete(
    "/hosts/{name}/team", status_code=204, summary="Send a host back to the queue (admins)"
)
def delete_host_team(name: str, viewer: ViewerDep, session: SessionDep) -> Response:
    try:
        unmap_host(session, viewer, name)
    except MappingError as exc:
        raise _mapping_error(exc) from None
    return Response(status_code=204)


class TeamOut(BaseModel):
    name: str
    public: bool
    hosts: int
    reports: int
    retention: str


class TeamList(BaseModel):
    teams: list[TeamOut]


class NewTeam(BaseModel):
    name: str


class TeamChange(BaseModel):
    public: bool


def _team_out(session: Session, viewer: Viewer, name: str) -> TeamOut:
    row = next(r for r in list_teams(session, viewer) if r.name == name)
    return TeamOut(**row.__dict__)


@router.get("/teams", summary="Teams you can see")
def teams(viewer: ViewerDep, session: SessionDep) -> TeamList:
    """All teams for admins and auditors; your own and the public ones for others."""
    return TeamList(teams=[TeamOut(**r.__dict__) for r in list_teams(session, viewer)])


@router.post("/teams", status_code=201, summary="Create a team ahead of sign-in (admins)")
def new_team(request: NewTeam, viewer: ViewerDep, session: SessionDep) -> TeamOut:
    if not viewer.is_admin:
        raise FORBIDDEN
    try:
        team = create_team(session, actor_of(viewer), request.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _team_out(session, viewer, team.name)


@router.patch(
    "/teams/{name}", summary="Make a team public or private (admins, the team's leads)",
    responses=TEAM_NOT_FOUND,
)  # fmt: skip
def change_team(name: str, request: TeamChange, viewer: ViewerDep, session: SessionDep) -> TeamOut:
    team = _team(session, viewer, name)
    if not may_set_visibility(viewer, team.name):
        raise FORBIDDEN
    set_visibility(session, actor_of(viewer), team, request.public)
    return _team_out(session, viewer, team.name)


@router.delete(
    "/reports/{report_id}",
    status_code=204,
    summary="Delete a report (admins; leads, their team's)",
    responses=NOT_FOUND,
)
def remove_report(report_id: int, viewer: ViewerDep, session: SessionDep) -> Response:
    try:
        deleted = delete_report(session, viewer, report_id, actor_of(viewer))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    if not deleted:
        raise HTTPException(404, "no such report")
    return Response(status_code=204)


class SettingsBody(BaseModel):
    retention_days: int | None = Field(description="None: keep reports forever")
    only_admins_map: bool
    max_upload_bytes: int


class RetentionBody(BaseModel):
    days: int | None = Field(description="With forever false: None inherits")
    forever: bool = False


@router.get("/settings", summary="Global settings (admins and auditors)")
def global_settings(viewer: ViewerDep, session: SessionDep) -> SettingsBody:
    if not viewer.reads_all:
        raise FORBIDDEN
    return SettingsBody(**get_settings(session).__dict__)


@router.put("/settings", summary="Change the global settings (admins)")
def put_settings(request: SettingsBody, viewer: ViewerDep, session: SessionDep) -> SettingsBody:
    if not may_change_settings(viewer):
        raise FORBIDDEN
    try:
        new = update_settings(session, actor_of(viewer), **request.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return SettingsBody(**new.__dict__)


def _set_retention(session: Session, viewer: Viewer, request: RetentionBody, **target: Any) -> None:
    if not may_set_retention(viewer):
        raise FORBIDDEN
    try:
        set_retention(session, actor_of(viewer), days=request.days, forever=request.forever,
                      **target)  # fmt: skip
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/teams/{name}/retention", summary="A team's retention (admins)",
            responses=TEAM_NOT_FOUND)  # fmt: skip
def put_team_retention(
    name: str, request: RetentionBody, viewer: ViewerDep, session: SessionDep
) -> RetentionBody:
    _set_retention(session, viewer, request, team=_team(session, viewer, name))
    return request


@router.put("/hosts/{name}/retention", summary="A host's retention (admins)",
            responses={404: {"description": "No such host"}})  # fmt: skip
def put_host_retention(
    name: str, request: RetentionBody, viewer: ViewerDep, session: SessionDep
) -> RetentionBody:
    host = session.scalar(select(Host).where(Host.name == name.lower()))
    if host is None:
        raise HTTPException(404, "no such host")
    _set_retention(session, viewer, request, host=host)
    return request


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    target: str
    details: dict[str, Any]


class AuditList(BaseModel):
    entries: list[AuditOut]
    next_before: int | None


@router.get("/audit", summary="The audit log, newest first (admins and auditors)")
def audit_log(
    viewer: ViewerDep, session: SessionDep, action: str | None = None, before: int | None = None
) -> AuditList:
    """`action` keeps the actions starting with it, e.g. `token.` or `host.map`."""
    if not may_read_audit(viewer):
        raise FORBIDDEN
    entries, next_before = page_of(
        list_audit(session, action=action, before=before, limit=PAGE_SIZE + 1), PAGE_SIZE
    )
    return AuditList(
        entries=[AuditOut.model_validate(e, from_attributes=True) for e in entries],
        next_before=next_before,
    )


CONFIG_NOT_FOUND = "no such config for this token"


@router.get(
    "/configs/{team}/{name}",
    summary="A host fetches its config (upload token)",
    response_class=Response,
    responses={
        200: {"content": {"application/yaml": {}}, "description": "The config, with an ETag"},
        304: {"description": "Unchanged since the ETag in If-None-Match"},
        401: {"description": "No valid upload token"},
        404: {"description": "No such config, or not one this token may fetch"},
        409: {"description": "The stored config no longer passes pydebuprep's rules"},
    },
)
def host_config(
    request: Request,
    team: str,
    name: str,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """What `pydebuprep --config https://PORTAL/api/v1/configs/<team>/<name>` reads. A global
    token fetches any team's configs; a team or host token, its own team's."""
    now = datetime.now(UTC)
    try:
        token = authenticate(session, authorization, now)
    except TokenError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from None
    config = session.scalar(
        select(Config).join(Config.team).where(Team.name == team, Config.name == name)
    )
    if config is None or (token.kind != "global" and token.team_id != config.team_id):
        raise HTTPException(404, CONFIG_NOT_FOUND)
    settings = request.app.state.settings
    problems = served_problems(settings, config, newest(config))
    if problems:
        # Never serve what the CLI would refuse: an error keeps hosts on their saved copy.
        log.warning("config %s/%s no longer passes: %s", team, name, "; ".join(problems))
        raise HTTPException(409, "this config no longer passes pydebuprep's rules; save it again")
    text = render_served(settings, config, newest(config))
    tag = etag(text)
    token.last_used_at = now
    session.commit()
    if if_none_match == tag:
        return Response(status_code=304, headers={"ETag": tag})
    return Response(
        text,
        media_type="application/yaml; charset=utf-8",
        headers={"ETag": tag, "Cache-Control": "no-cache"},
    )


class ConfigSummary(BaseModel):
    name: str
    version: int
    updated_at: datetime
    updated_by: str
    url: str = Field(description="What hosts pass to pydebuprep --config")


class ConfigList(BaseModel):
    configs: list[ConfigSummary]


class NewConfig(BaseModel):
    name: str
    values: dict[str, Any] | None = Field(
        None, description="Like the config file's report, logs and checks; default: none"
    )


class ConfigValues(BaseModel):
    values: Any = Field(description="Like the config file's report, logs and checks")


class CreatedConfig(BaseModel):
    name: str
    version: int
    url: str


class SavedConfig(BaseModel):
    version: int
    changed: bool


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    created_by: str


class ConfigDetail(BaseModel):
    team: str
    name: str
    url: str
    version: int
    values: dict[str, Any]
    versions: list[VersionOut]


CONFIG_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Your roles only let you read it"},
    404: {"description": "No such team or config, or not visible to you"},
    422: {"description": "The config isn't one hosts would accept; detail lists why"},
}


def _config_error(exc: ConfigError) -> HTTPException:
    return HTTPException(exc.status, exc.errors if exc.status == 422 else exc.detail)


def _readable(session: Session, viewer: Viewer, team: str, name: str) -> Config:
    config = get_config(session, viewer, team, name)
    if config is None:
        raise HTTPException(404, "no such config, or you can't see it")
    return config


@router.get("/teams/{team}/configs", summary="A team's configs", responses=CONFIG_ERRORS)
def team_configs(request: Request, team: str, viewer: ViewerDep, session: SessionDep) -> ConfigList:
    if get_team(session, team) is None or not may_read_configs(viewer, team):
        raise HTTPException(404, "no such team, or you can't see its configs")
    settings = request.app.state.settings
    return ConfigList(
        configs=[
            ConfigSummary(
                name=row.name,
                version=row.version,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
                url=served_url(settings, team, row.name),
            )
            for row in list_configs(session, viewer)
            if row.team == team
        ]
    )


@router.post(
    "/teams/{team}/configs", status_code=201, summary="Create a config", responses=CONFIG_ERRORS
)
def new_config(
    request: Request, team: str, body: NewConfig, viewer: ViewerDep, session: SessionDep
) -> CreatedConfig:
    settings = request.app.state.settings
    try:
        config = create_config(session, viewer, settings, team, body.name, body.values)
    except ConfigError as exc:
        raise _config_error(exc) from None
    return CreatedConfig(name=config.name, version=1, url=served_url(settings, team, config.name))


@router.get("/teams/{team}/configs/{name}", summary="A config", responses=CONFIG_ERRORS)
def config_detail(
    request: Request, team: str, name: str, viewer: ViewerDep, session: SessionDep
) -> ConfigDetail:
    config = _readable(session, viewer, team, name)
    current = newest(config)
    return ConfigDetail(
        team=team,
        name=name,
        url=served_url(request.app.state.settings, team, name),
        version=current.version,
        values=current.values,
        versions=[VersionOut.model_validate(v, from_attributes=True) for v in config.versions],
    )


@router.put(
    "/teams/{team}/configs/{name}",
    summary="Save a config as its next version",
    responses=CONFIG_ERRORS,
)
def put_config(
    request: Request, team: str, name: str, body: ConfigValues, viewer: ViewerDep,
    session: SessionDep,
) -> SavedConfig:  # fmt: skip
    """Saving what it has already makes no new version (`changed` false)."""
    try:
        saved = save_config(session, viewer, request.app.state.settings, team, name, body.values)
    except ConfigError as exc:
        raise _config_error(exc) from None
    if saved is None:
        return SavedConfig(version=newest(_readable(session, viewer, team, name)).version,
                           changed=False)  # fmt: skip
    return SavedConfig(version=saved.version, changed=True)


@router.get(
    "/teams/{team}/configs/{name}/versions/{number}",
    summary="A config version, as hosts got it",
    response_class=Response,
    responses={200: {"content": {"application/yaml": {}}}, **CONFIG_ERRORS},
)
def config_version(
    request: Request, team: str, name: str, number: int, viewer: ViewerDep, session: SessionDep
) -> Response:
    config = _readable(session, viewer, team, name)
    version = get_version(config, number)
    if version is None:
        raise HTTPException(404, "no such version")
    text = render_served(request.app.state.settings, config, version)
    return Response(text, media_type="application/yaml; charset=utf-8")
