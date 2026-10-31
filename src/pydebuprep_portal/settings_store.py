"""The admins' global settings, kept in the database."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from pydebuprep_portal.audit import record
from pydebuprep_portal.models import Setting

MAX_RETENTION_DAYS = 36500
MIN_UPLOAD_BYTES = 1024
MAX_UPLOAD_BYTES = 2**30


@dataclass(frozen=True)
class GlobalSettings:
    retention_days: int | None  # None: keep reports forever
    only_admins_map: bool
    max_upload_bytes: int


def _row(session: Session) -> Setting:
    row = session.get(Setting, 1)
    if row is None:  # pragma: no cover - the migration inserts it
        raise RuntimeError("the settings row is missing; run the migrations")
    return row


def get_settings(session: Session) -> GlobalSettings:
    row = _row(session)
    return GlobalSettings(row.retention_days, row.only_admins_map, row.max_upload_bytes)


def update_settings(
    session: Session,
    actor: str,
    *,
    retention_days: int | None,
    only_admins_map: bool,
    max_upload_bytes: int,
) -> GlobalSettings:
    """Change the settings (ValueError says what is wrong); audited when anything changed."""
    if retention_days is not None and not 1 <= retention_days <= MAX_RETENTION_DAYS:
        raise ValueError(f"retention is 1 to {MAX_RETENTION_DAYS} days, or keep forever")
    if not MIN_UPLOAD_BYTES <= max_upload_bytes <= MAX_UPLOAD_BYTES:
        raise ValueError(f"the upload limit is {MIN_UPLOAD_BYTES} bytes to 1 GiB")
    old = get_settings(session)
    new = GlobalSettings(retention_days, only_admins_map, max_upload_bytes)
    if new != old:
        row = _row(session)
        row.retention_days = retention_days
        row.only_admins_map = only_admins_map
        row.max_upload_bytes = max_upload_bytes
        record(session, actor, "settings.update", "settings", old=asdict(old), new=asdict(new))
        session.commit()
    return new
