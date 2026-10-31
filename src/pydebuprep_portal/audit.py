"""The audit log: every change to tokens, mappings, team visibility, retention, configs
and settings, with who and when."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import AuditEntry


def actor_of(viewer: Viewer) -> str:
    return f"{viewer.name} <{viewer.subject}>"


def record(session: Session, actor: str, action: str, target: str, **details: object) -> None:
    """Add an entry to the caller's transaction: it is kept only if the change is."""
    session.add(
        AuditEntry(at=datetime.now(UTC), actor=actor, action=action, target=target, details=details)
    )


def list_audit(session: Session, *, before: int | None = None, limit: int = 50) -> list[AuditEntry]:
    query = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)
    if before is not None:
        query = query.where(AuditEntry.id < before)
    return list(session.scalars(query))
