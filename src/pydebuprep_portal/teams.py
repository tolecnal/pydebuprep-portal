"""Teams: created at sign-in from the provider's groups, or by admins ahead of time."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer, may_read_team
from pydebuprep_portal.models import Team


def ensure_teams(session: Session, names: Iterable[str]) -> None:
    """Create the teams that don't exist yet: private, by "sign-in"."""
    rows = [
        {"name": name, "public": False, "keep_forever": False,
         "created_at": datetime.now(UTC), "created_by": "sign-in"}
        for name in sorted(set(names))
    ]  # fmt: skip
    if rows:
        session.execute(insert(Team).values(rows).on_conflict_do_nothing(index_elements=["name"]))
        session.commit()


def get_team(session: Session, name: str) -> Team | None:
    return session.scalar(select(Team).where(Team.name == name))


def visible_team(session: Session, viewer: Viewer, name: str) -> Team | None:
    """The team, or None when it doesn't exist or VIEWER may not see it."""
    team = get_team(session, name)
    if team is None or not may_read_team(viewer, team.name, team.public):
        return None
    return team
