"""The portal's command line: tokens and migrations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from conftest import BASE_ENV
from pydebuprep_portal.manage import main
from pydebuprep_portal.models import Token
from pydebuprep_portal.tokens import authenticate


def test_manage_create_token_prints_it_once(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-token", "bootstrap"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    token = authenticate(db, f"Bearer {out.strip()}", datetime.now(UTC))
    assert (token.kind, token.name, token.created_by, token.expires_at) == (
        "global",
        "bootstrap",
        "manage",
        None,
    )


def test_manage_create_token_with_expiry(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-token", "short", "--expires-days", "7"]) == 0
    capsys.readouterr()
    token = db.query(Token).one()
    assert token.expires_at is not None
    assert timedelta(days=6) < token.expires_at - datetime.now(UTC) <= timedelta(days=7)


def test_manage_migrate(db_env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope"],
        ["create-token"],
        ["create-token", "x", "--expires-days", "0"],
        [
            "create-token",
            "",
        ],
    ],
)
def test_manage_usage_errors(
    db_env: dict[str, str], argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_manage_reports_bad_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PORTAL_DATABASE_URL")
    assert main(["migrate"]) == 1
    assert "PORTAL_DATABASE_URL" in capsys.readouterr().err
