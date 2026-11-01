"""What a viewer may see: reports, hosts and findings."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Select, func, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session, aliased, contains_eager, joinedload

from pydebuprep_portal.access import Viewer, may_delete_reports
from pydebuprep_portal.audit import record
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
    machine_id: str | None = None


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
        machine_id=report.machine_id,
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


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


def _contains(
    column: InstrumentedAttribute[str] | ColumnElement[Any], text: str
) -> ColumnElement[bool]:
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
    machines: int  # different machine IDs among its reports: more than one is a clash
    release: str = ""  # of the newest report: "debian 13 (trixie)"
    codename: str = ""
    kernel: str = ""


def list_hosts(
    session: Session,
    viewer: Viewer,
    *,
    name: str | None = None,
    worse_only: bool = False,
    release: str | None = None,
    kernel: str | None = None,
    silent_days: int | None = None,
    now: datetime | None = None,
) -> list[HostRow]:
    """Every host with a visible report: its newest visible one (by generated time) and the
    worst of the one before. RELEASE is the newest report's codename, KERNEL part of its
    kernel; SILENT_DAYS keeps the hosts whose newest report is older than that."""
    ranked = (
        select(
            Report.id,
            Report.host_id,
            Report.generated_at,
            Report.worst,
            Report.os_id,
            Report.os_version,
            Report.codename,
            Report.kernel,
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
    machines = (
        select(Report.host_id, func.count(Report.machine_id.distinct()).label("machines"))
        .where(visible(viewer))
        .group_by(Report.host_id)
        .subquery()
    )
    query = (
        select(
            Host.name, newest.c.id, newest.c.generated_at, newest.c.worst, previous.c.worst,
            machines.c.machines, newest.c.os_id, newest.c.os_version, newest.c.codename,
            newest.c.kernel,
        )
        .join(newest, (newest.c.host_id == Host.id) & (newest.c.rank == 1))
        .outerjoin(previous, (previous.c.host_id == Host.id) & (previous.c.rank == 2))
        .join(machines, machines.c.host_id == Host.id)
        .order_by(Host.name)
    )  # fmt: skip
    if name:
        query = query.where(_contains(Host.name, name))
    if release:
        query = query.where(newest.c.codename == release)
    if kernel:
        query = query.where(_contains(newest.c.kernel, kernel))
    if silent_days is not None:
        cutoff = (now or datetime.now().astimezone()) - timedelta(days=silent_days)
        query = query.where(newest.c.generated_at < cutoff)
    rows = [
        HostRow(
            name=host,
            last_report_id=report_id,
            last_generated_at=generated_at,
            worst=worst,
            previous_worst=before,
            worse=before is not None and SEVERITY_ORDER[worst] > SEVERITY_ORDER[before],
            machines=count,
            release=f"{os_id} {os_version} ({codename})",
            codename=codename,
            kernel=kernel_name,
        )
        for (
            host, report_id, generated_at, worst, before, count, os_id, os_version, codename,
            kernel_name,
        ) in session.execute(query)
    ]  # fmt: skip
    return [r for r in rows if r.worse] if worse_only else rows


def release_counts(rows: list[HostRow]) -> list[tuple[str, int]]:
    """How many of ROWS run each release, most common first."""
    counts = Counter(r.release for r in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


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
    since: date | None = None,
    until: date | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[FindingHit]:
    """Findings, newest first; every word must appear in the title, the detail or the
    evidence. SINCE and UNTIL are the first and last day (UTC) of the reports."""
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
        query = query.where(or_(_contains(Finding.title, word), _contains(Finding.text, word)))
    if since is not None:
        query = query.where(Report.generated_at >= _day_start(since))
    if until is not None:
        query = query.where(Report.generated_at < _day_start(until + timedelta(days=1)))
    if before is not None:
        query = query.where(Finding.id < before)
    return [FindingHit(*row) for row in session.execute(query)]


def delete_report(session: Session, viewer: Viewer, report_id: int, actor: str) -> bool:
    """Delete a report; False when it doesn't exist or VIEWER can't see it, PermissionError
    when they see it but may not delete it."""
    report = get_report(session, viewer, report_id)
    if report is None:
        return False
    if not may_delete_reports(viewer, report.team.name if report.team else None):
        raise PermissionError("your roles don't allow deleting this report")
    record(session, actor, "report.delete", report.host.name, id=report.id,
           generated_at=report.generated_at.isoformat(),
           team=report.team.name if report.team else None)  # fmt: skip
    session.delete(report)
    session.commit()
    return True
