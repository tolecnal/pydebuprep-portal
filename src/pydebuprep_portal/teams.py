"""Teams: created at sign-in from the provider's groups, or by admins ahead of time."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer, may_read_team, valid_team_name
from pydebuprep_portal.audit import record
from pydebuprep_portal.models import Host, Report, Team
from pydebuprep_portal.retention import describe, team_retention
from pydebuprep_portal.settings_store import GlobalSettings, get_settings


def ensure_teams(session: Session, names: Iterable[str]) -> None:
    """Create the teams that don't exist yet: private, by "sign-in"."""
    rows = [
        {"name": name, "public": False, "keep_forever": False,
         "created_at": datetime.now(UTC), "created_by": "sign-in"}
        for name in sorted(set(names))
    ]  # fmt: skip
    if rows:
        created = session.scalars(
            insert(Team)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(Team.name)
        ).all()
        for name in created:
            record(session, "sign-in", "team.create", name)
        session.commit()


def get_team(session: Session, name: str) -> Team | None:
    return session.scalar(select(Team).where(Team.name == name))


def visible_team(session: Session, viewer: Viewer, name: str) -> Team | None:
    """The team, or None when it doesn't exist or VIEWER may not see it."""
    team = get_team(session, name)
    if team is None or not may_read_team(viewer, team.name, team.public):
        return None
    return team


@dataclass(frozen=True)
class TeamRow:
    name: str
    public: bool
    hosts: int
    reports: int
    retention: str


def retention_text(team: Team, settings: GlobalSettings) -> str:
    """A team's retention as people read it: its own, or the global one it inherits."""
    return describe(team_retention(team, settings))


def list_teams(session: Session, viewer: Viewer) -> list[TeamRow]:
    """All teams for admins and auditors; one's own and the public ones for others."""
    hosts = select(func.count()).where(Host.team_id == Team.id).scalar_subquery()
    reports = select(func.count()).where(Report.team_id == Team.id).scalar_subquery()
    query = select(Team, hosts, reports).order_by(Team.name)
    if not viewer.reads_all:
        query = query.where(or_(Team.name.in_(sorted(viewer.teams)), Team.public))
    settings = get_settings(session)
    return [
        TeamRow(team.name, team.public, n_hosts, n_reports, retention_text(team, settings))
        for team, n_hosts, n_reports in session.execute(query)
    ]


def team_hosts(session: Session, team: Team) -> list[str]:
    return list(
        session.scalars(select(Host.name).where(Host.team_id == team.id).order_by(Host.name))
    )


def create_team(session: Session, actor: str, name: str) -> Team:
    """A new, private team (admins); ValueError for a bad or taken name."""
    name = name.strip()
    if not valid_team_name(name):
        raise ValueError(
            "a team name is lower-case letters, digits and dashes (at most 63), not ending in -lead"
        )
    if get_team(session, name) is not None:
        raise ValueError(f"there is already a team {name}")
    team = Team(name=name, public=False, keep_forever=False,
                created_at=datetime.now(UTC), created_by=actor)  # fmt: skip
    session.add(team)
    record(session, actor, "team.create", name)
    session.commit()
    return team


def set_visibility(session: Session, actor: str, team: Team, public: bool) -> None:
    if team.public == public:
        return
    old = "public" if team.public else "private"
    team.public = public
    record(session, actor, "team.visibility", team.name, old=old,
           new="public" if public else "private")  # fmt: skip
    session.commit()


MAX_RETENTION_DAYS = 36500


def set_retention(
    session: Session,
    actor: str,
    *,
    team: Team | None = None,
    host: Host | None = None,
    days: int | None,
    forever: bool,
) -> None:
    """Set a team's or a host's retention (admins): DAYS, FOREVER, or neither to inherit."""
    target = team if team is not None else host
    if target is None:  # pragma: no cover - callers pass one
        raise ValueError("a team or a host")
    if forever:
        days = None
    elif days is not None and not 1 <= days <= MAX_RETENTION_DAYS:
        raise ValueError(f"retention is 1 to {MAX_RETENTION_DAYS} days")
    old = {"days": target.retention_days, "forever": target.keep_forever}
    new = {"days": days, "forever": forever}
    if old == new:
        return
    target.retention_days, target.keep_forever = days, forever
    label = f"team:{target.name}" if team is not None else f"host:{target.name}"
    record(session, actor, "retention.set", label, old=old, new=new)
    session.commit()
