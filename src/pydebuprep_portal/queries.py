"""What a viewer may see: reports, hosts and findings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Host, Report


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
    config_stale: bool


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
        config_stale=report.config_stale,
    )


def list_reports(
    session: Session,
    viewer: Viewer,
    *,
    worst: str | None = None,
    host: str | None = None,
    before: int | None = None,
    limit: int = 50,
) -> list[ReportRow]:
    """Newest received first. Step 2 has no teams: only admins and auditors see reports."""
    if not viewer.reads_all:
        return []
    query = (
        select(Report)
        .join(Report.host)
        .options(contains_eager(Report.host))
        .order_by(Report.id.desc())
        .limit(limit)
    )
    if worst is not None:
        query = query.where(Report.worst == worst)
    if host is not None:
        query = query.where(Host.name == host.lower())
    if before is not None:
        query = query.where(Report.id < before)
    return [report_row(r) for r in session.scalars(query)]


def get_report(session: Session, viewer: Viewer, report_id: int) -> Report | None:
    """The report, or None when it doesn't exist or the viewer may not see it."""
    if not viewer.reads_all:
        return None
    return session.get(Report, report_id)


def page_of(rows: list[ReportRow], size: int) -> tuple[list[ReportRow], int | None]:
    """ROWS fetched with limit SIZE + 1: the page, and the `before` of the next page."""
    if len(rows) > size:
        return rows[:size], rows[size - 1].id
    return rows, None
