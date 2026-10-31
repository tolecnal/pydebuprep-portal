"""Demo data for a local preview."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from pydebuprep_portal.manage import main
from pydebuprep_portal.models import Config, Host, Report, Team, Token


def test_demo_data_fills_an_empty_portal(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo-data"]) == 0
    out = capsys.readouterr().out
    assert "reports" in out
    teams = {t.name: t for t in db.query(Team)}
    assert {"web", "db", "docs"} <= set(teams) and teams["docs"].public
    assert db.query(Report).count() == 26  # every week of every story
    assert db.query(Host).filter(Host.team_id.is_(None)).count() >= 1  # something in the queue
    assert {r.worst for r in db.query(Report)} >= {"critical", "warning", "ok"}
    assert db.query(Config).count() >= 1 and db.query(Token).count() >= 2


def test_demo_data_refuses_a_portal_with_reports(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo-data"]) == 0
    capsys.readouterr()
    assert main(["demo-data"]) == 1
    assert "already has reports" in capsys.readouterr().err
