"""What a viewer may see: reports, hosts and findings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import ColumnElement, Select, func, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session, aliased, contains_eager, joinedload

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Finding, Host, Report, Team

UNASSIGNED = "unassigned"

SEVERITY_ORDER = {"ok": 0, "info": 1, "warning": 2, "critical": 3}


class HasId(Protocol):
    @property
    def id(self) -> int: ...


@dataclass(frozen=True)
class ReportRow:
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
    team: str | None
    flagged_team: str | None  # sent with this other team's token


def visible(viewer: Viewer) -> ColumnElement[bool]:
    """The reports VIEWER may see: everything for admins and auditors; otherwise the
    reports of their own teams and of public teams, never the unassigned queue."""
    if viewer.reads_all:
        return true()
    teams = select(Team.id).where(or_(Team.name.in_(sorted(viewer.teams)), Team.public))
    return Report.team_id.in_(teams)


def visible_team_names(session: Session, viewer: Viewer) -> list[str]:
    query = select(Team.name).order_by(Team.name)
    if not viewer.reads_all:
        query = query.where(or_(Team.name.in_(sorted(viewer.teams)), Team.public))
    return list(session.scalars(query))


def report_row(report: Report) -> ReportRow:
    return ReportRow(
        id=report.id,
        host=report.host.name,
        generated_at=report.generated_at,
        received_at=report.received_at,
        worst=report.worst,
        critical=report.critical,
        warning=report.warning,
        info=report.info,
        error=report.error,
        os=f"{report.os_id} {report.os_version} ({report.codename})",
        kernel=report.kernel,
        config_stale=report.config_stale,
        team=report.team.name if report.team else None,
        flagged_team=report.token_team.name if report.token_team else None,
    )


def _reports(viewer: Viewer) -> Select[Report]:
    """Visible reports with their host and teams loaded."""
    return (
        select(Report)
        .join(Report.host)
        .options(
            contains_eager(Report.host), joinedload(Report.team), joinedload(Report.token_team)
        )
        .where(visible(viewer))
    )


def list_reports(
    session: Session,
    viewer: Viewer,
    *,
    worst: str | None = None,
    host: str | None = None,
    team: str | None = None,
    flagged: bool = False,
    before: int | None = None,
    limit: int = 50,
) -> list[ReportRow]:
    """Newest received first; TEAM is a team's name or "unassigned" for the queue."""
    query = _reports(viewer).order_by(Report.id.desc()).limit(limit)
    if team == UNASSIGNED:
        query = query.where(Report.team_id.is_(None))
    elif team is not None:
        query = query.where(Report.team_id.in_(select(Team.id).where(Team.name == team)))
    if flagged:
        query = query.where(Report.token_team_id.is_not(None))
    if worst is not None:
        query = query.where(Report.worst == worst)
    if host is not None:
        query = query.where(Host.name == host.lower())
    if before is not None:
        query = query.where(Report.id < before)
    return [report_row(r) for r in session.scalars(query)]


def get_report(session: Session, viewer: Viewer, report_id: int) -> Report | None:
    """The report, or None when it doesn't exist or the viewer may not see it."""
    return session.scalar(_reports(viewer).where(Report.id == report_id))


def page_of[R: HasId](rows: list[R], size: int) -> tuple[list[R], int | None]:
    """ROWS fetched with limit SIZE + 1: the page, and the `before` of the next page."""
    if len(rows) > size:
        return rows[:size], rows[size - 1].id
    return rows, None


def _contains(column: InstrumentedAttribute[str], text: str) -> ColumnElement[bool]:
    """Case-insensitive substring match; %, _ and \\ in TEXT match themselves."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


@dataclass(frozen=True)
class HostRow:
    name: str
    last_report_id: int
    last_generated_at: datetime
    worst: str
    previous_worst: str | None
    worse: bool


def list_hosts(
    session: Session, viewer: Viewer, *, name: str | None = None, worse_only: bool = False
) -> list[HostRow]:
    """Every host with a visible report: its newest visible one (by generated time) and the
    worst of the one before."""
    ranked = (
        select(
            Report.id,
            Report.host_id,
            Report.generated_at,
            Report.worst,
            func.row_number()
            .over(
                partition_by=Report.host_id, order_by=(Report.generated_at.desc(), Report.id.desc())
            )
            .label("rank"),
        )
        .where(visible(viewer))
        .subquery()
    )
    newest = aliased(ranked)
    previous = aliased(ranked)
    query = (
        select(Host.name, newest.c.id, newest.c.generated_at, newest.c.worst, previous.c.worst)
        .join(newest, (newest.c.host_id == Host.id) & (newest.c.rank == 1))
        .outerjoin(previous, (previous.c.host_id == Host.id) & (previous.c.rank == 2))
        .order_by(Host.name)
    )
    if name:
        query = query.where(_contains(Host.name, name))
    rows = [
        HostRow(
            name=host,
            last_report_id=report_id,
            last_generated_at=generated_at,
            worst=worst,
            previous_worst=before,
            worse=before is not None and SEVERITY_ORDER[worst] > SEVERITY_ORDER[before],
        )
        for host, report_id, generated_at, worst, before in session.execute(query)
    ]
    return [r for r in rows if r.worse] if worse_only else rows


def host_timeline(session: Session, viewer: Viewer, name: str) -> list[ReportRow] | None:
    """A host's reports, oldest first; None if it has none the viewer may see."""
    query = (
        _reports(viewer).where(Host.name == name.lower()).order_by(Report.generated_at, Report.id)
    )
    rows = [report_row(r) for r in session.scalars(query)]
    return rows or None


@dataclass(frozen=True)
class FindingHit:
    id: int
    report_id: int
    host: str
    generated_at: datetime
    check_id: str
    severity: str
    title: str


def search_findings(
    session: Session,
    viewer: Viewer,
    *,
    host: str | None = None,
    check: str | None = None,
    severity: str | None = None,
    words: str | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[FindingHit]:
    """Findings, newest first; every word must appear in the title."""
    query = (
        select(
            Finding.id,
            Report.id,
            Host.name,
            Report.generated_at,
            Finding.check_id,
            Finding.severity,
            Finding.title,
        )
        .join(Finding.report)
        .join(Report.host)
        .where(visible(viewer))
        .order_by(Finding.id.desc())
        .limit(limit)
    )
    if host:
        query = query.where(_contains(Host.name, host))
    if check:
        query = query.where(Finding.check_id == check)
    if severity:
        query = query.where(Finding.severity == severity)
    for word in (words or "").split():
        query = query.where(_contains(Finding.title, word))
    if before is not None:
        query = query.where(Finding.id < before)
    return [FindingHit(*row) for row in session.execute(query)]
