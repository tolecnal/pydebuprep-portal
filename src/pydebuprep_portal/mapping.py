"""The unassigned queue, and mapping hosts to teams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session, aliased

from pydebuprep_portal.access import Viewer, may_map, may_read_team, may_see_queue_hosts
from pydebuprep_portal.audit import actor_of, record
from pydebuprep_portal.models import Host, Report, Team
from pydebuprep_portal.settings_store import get_settings
from pydebuprep_portal.teams import get_team


class MappingError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class QueuedHost:
    name: str
    os: str
    first_seen: datetime
    last_seen: datetime
    reports: int
    worst: str


def queue(session: Session, viewer: Viewer) -> list[QueuedHost]:
    """Unmapped hosts with their newest report's OS and worst severity. Leads see these
    rows (to map hosts) but not the reports."""
    if not may_see_queue_hosts(viewer):
        return []
    ranked = (
        select(
            Report.host_id,
            Report.os_id,
            Report.os_version,
            Report.codename,
            Report.worst,
            func.count().over(partition_by=Report.host_id).label("reports"),
            func.row_number()
            .over(
                partition_by=Report.host_id, order_by=(Report.generated_at.desc(), Report.id.desc())
            )
            .label("rank"),
        )
        .where(Report.team_id.is_(None))
        .subquery()
    )
    newest = aliased(ranked)
    query = (
        select(Host, newest)
        .join(newest, (newest.c.host_id == Host.id) & (newest.c.rank == 1))
        .where(Host.team_id.is_(None))
        .order_by(Host.last_seen.desc(), Host.name)
    )
    return [
        QueuedHost(
            name=row.Host.name,
            os=f"{row.os_id} {row.os_version} ({row.codename})",
            first_seen=row.Host.first_seen,
            last_seen=row.Host.last_seen,
            reports=row.reports,
            worst=row.worst,
        )
        for row in session.execute(query)
    ]


def _host(session: Session, viewer: Viewer, name: str) -> Host:
    """The host, locked for the change; 404 when it doesn't exist or VIEWER can't see it."""
    host = session.scalar(select(Host).where(Host.name == name.lower()).with_for_update())
    if host is not None and host.team_id is None and may_see_queue_hosts(viewer):
        return host
    if (
        host is not None
        and host.team is not None
        and may_read_team(viewer, host.team.name, host.team.public)
    ):
        return host
    session.rollback()
    raise MappingError(404, "no such host")


def _move(session: Session, host: Host, team_id: int | None) -> int:
    """Move every report of HOST to TEAM_ID (None: the queue); the number moved."""
    result = session.execute(
        update(Report).where(Report.host_id == host.id).values(team_id=team_id)
    )
    moved = cast(CursorResult[Any], result).rowcount
    if team_id is not None:  # a flag for the team the reports now belong to is moot
        session.execute(
            update(Report)
            .where(Report.host_id == host.id, Report.token_team_id == team_id)
            .values(token_team_id=None)
        )
    host.team_id = team_id
    return moved


def map_host(session: Session, viewer: Viewer, host_name: str, team_name: str) -> int:
    """Map a host to a team, moving its reports; the number of reports moved."""
    host = _host(session, viewer, host_name)
    team = get_team(session, team_name)
    if team is None:
        session.rollback()
        raise MappingError(404, "no such team")
    allowed = may_map(
        viewer,
        team.name,
        host_mapped=host.team_id is not None,
        only_admins_map=get_settings(session).only_admins_map,
    )
    if not allowed:
        session.rollback()
        raise MappingError(403, "your roles don't allow mapping this host to this team")
    if host.team_id == team.id:
        session.rollback()
        return 0
    old = host.team.name if host.team else None
    moved = _move(session, host, team.id)
    record(session, actor_of(viewer), "host.map", host.name, team=team.name, **{"from": old},
           reports_moved=moved)  # fmt: skip
    session.commit()
    return moved


def unmap_host(session: Session, viewer: Viewer, host_name: str) -> int:
    """Send a host and its reports back to the queue (admins); the number moved."""
    host = _host(session, viewer, host_name)
    if not viewer.is_admin:
        session.rollback()
        raise MappingError(403, "only admins unmap hosts")
    if host.team_id is None:
        session.rollback()
        return 0
    old = host.team.name if host.team else None
    moved = _move(session, host, None)
    record(session, actor_of(viewer), "host.unmap", host.name, **{"from": old},
           reports_moved=moved)  # fmt: skip
    session.commit()
    return moved


def team_choices(session: Session, viewer: Viewer) -> list[str]:
    """The teams VIEWER may map queued hosts into."""
    if viewer.is_admin:
        return list(session.scalars(select(Team.name).order_by(Team.name)))
    if get_settings(session).only_admins_map:
        return []
    return sorted(viewer.leads)
