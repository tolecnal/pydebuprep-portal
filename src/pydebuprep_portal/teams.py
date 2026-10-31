"""Teams: created at sign-in from the provider's groups, or by admins ahead of time."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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
