"""The admins' global settings: retention, who maps hosts, the upload limit."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import set_upload_limit
from pydebuprep_portal.models import AuditEntry
from pydebuprep_portal.settings_store import GlobalSettings, get_settings, update_settings

DEFAULTS = {"retention_days": 365, "only_admins_map": False, "max_upload_bytes": 10485760}


def test_default_settings(db: Session) -> None:
    assert get_settings(db) == GlobalSettings(**DEFAULTS)


@pytest.mark.parametrize(
    "bad",
    [
        {"retention_days": 0},
        {"retention_days": 36501},
        {"max_upload_bytes": 1023},
        {"max_upload_bytes": 2**30 + 1},
    ],
)
def test_bad_settings(db: Session, bad: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        update_settings(db, "admin", **(DEFAULTS | bad))  # type: ignore[arg-type]
    assert get_settings(db) == GlobalSettings(**DEFAULTS)
    assert db.query(AuditEntry).count() == 0


def test_settings_update_is_audited(db: Session) -> None:
    new = DEFAULTS | {"retention_days": None, "only_admins_map": True}
    assert update_settings(db, "Ada <user-1>", **new) == GlobalSettings(**new)  # type: ignore[arg-type]
    entry = db.query(AuditEntry).one()
    assert (entry.actor, entry.action, entry.target) == (
        "Ada <user-1>",
        "settings.update",
        "settings",
    )
    assert entry.details == {"old": DEFAULTS, "new": new}


def test_unchanged_settings_are_not_audited(db: Session) -> None:
    update_settings(db, "admin", **DEFAULTS)  # type: ignore[arg-type]
    assert db.query(AuditEntry).count() == 0


def test_upload_limit_comes_from_the_settings(
    make_client: Callable[..., TestClient], token: str, db: Session
) -> None:
    set_upload_limit(db, 2000)
    resp = make_client().post(
        "/api/v1/reports",
        content=b"{" + b" " * 3000 + b"}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert "2000" in resp.json()["detail"]
