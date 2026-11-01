"""Demo data for a local preview."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from pydebuprep_portal.manage import main
from pydebuprep_portal.models import Config, Finding, Host, Report, Team, Token


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
    # Something for each search: a host name two machines report, a host left on the
    # old release, and findings with details.
    machines = {(r.host.name, r.machine_id) for r in db.query(Report)}
    assert len({m for h, m in machines if h == "web2"}) == 2
    assert {
        r.codename for r in db.query(Report).join(Report.host).filter(Host.name == "docs1")
    } == {"bookworm"}
    assert db.query(Finding).filter(Finding.text.contains("php8.2-fpm.sock")).count() >= 1


def test_demo_data_refuses_a_portal_with_reports(
    db: Session, db_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo-data"]) == 0
    capsys.readouterr()
    assert main(["demo-data"]) == 1
    assert "already has reports" in capsys.readouterr().err
