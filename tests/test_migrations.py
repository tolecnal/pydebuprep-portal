"""The migrations build the schema the models describe."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pydebuprep_portal.db import Base, migrations_config
from pydebuprep_portal.models import Finding, Host, Report, Team, Token

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)


def test_upgrade_from_empty_creates_the_tables(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == {
        "hosts",
        "tokens",
        "reports",
        "findings",
        "teams",
        "settings",
        "audit_log",
        "alembic_version",
    }


def test_models_match_the_migration(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_downgrade_to_base_and_up_again(engine: Engine, db_url: str) -> None:
    config = migrations_config(db_url)
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")
    assert "reports" in inspect(engine).get_table_names()


def add_report(db: Session, sha256: str = "a" * 64) -> Report:
    host = db.query(Host).filter_by(name="web1").one_or_none()
    if host is None:
        host = Host(name="web1", first_seen=NOW, last_seen=NOW)
        db.add(host)
    token = db.query(Token).filter_by(public_id="0123abcd").one_or_none()
    if token is None:
        token = Token(kind="global", name="t", public_id="0123abcd", secret_hash="0" * 64,
                      created_at=NOW, created_by="test")  # fmt: skip
        db.add(token)
    report = Report(host=host, token=token, sha256=sha256, generated_at=NOW, received_at=NOW,
                    worst="warning", critical=0, warning=1, info=0, error=0, os_id="debian",
                    os_version="13", codename="trixie", kernel="6.12", tool_version="0.5.0",
                    config_stale=False, body="{}")  # fmt: skip
    report.findings.append(Finding(check_id="disk-space", severity="warning", title="Disk"))
    db.add(report)
    db.commit()
    return report


def test_report_sha256_is_unique(db: Session) -> None:
    add_report(db)
    with pytest.raises(IntegrityError):
        add_report(db)


def test_deleting_a_report_deletes_its_findings(db: Session) -> None:
    report = add_report(db)
    db.delete(report)
    db.commit()
    assert db.query(Finding).count() == 0


def test_worst_and_kind_are_checked(db: Session) -> None:
    report = add_report(db)
    report.worst = "bad"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(Token(kind="other", name="t", public_id="99999999", secret_hash="0" * 64,
                 created_at=NOW, created_by="test"))  # fmt: skip
    with pytest.raises(IntegrityError):
        db.commit()


def test_upgrade_from_0001_keeps_step2_data(engine: Engine, db_url: str) -> None:
    config = migrations_config(db_url)
    command.downgrade(config, "0001")
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE hosts, tokens, reports, findings RESTART IDENTITY CASCADE"))
            conn.execute(text(
                "INSERT INTO hosts (name, first_seen, last_seen) VALUES ('web1', now(), now());"
                "INSERT INTO tokens (kind, name, public_id, secret_hash, created_at, created_by)"
                " VALUES ('global', 't', '0123abcd', 'x', now(), 'test');"
                "INSERT INTO reports (host_id, token_id, sha256, generated_at, received_at, worst,"
                " critical, warning, info, error, os_id, os_version, codename, kernel, tool_version,"
                " config_stale, body) VALUES (1, 1, 'a', now(), now(), 'ok', 0, 0, 0, 0, 'debian',"
                " '13', 'trixie', '6.12', '0.5.0', false, '{}')"
            ))  # fmt: skip
    finally:
        command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT team_id, token_team_id FROM reports")).one() == (
            None,
            None,
        )
        assert conn.execute(text("SELECT team_id, host_name FROM tokens")).one() == (None, None)
        assert conn.execute(text("SELECT team_id, keep_forever FROM hosts")).one() == (None, False)
        assert conn.execute(text("SELECT count(*) FROM settings")).scalar() == 1


def test_token_kind_constraints(db: Session) -> None:
    team = Team(name="web", public=False, keep_forever=False, created_at=NOW, created_by="test")
    db.add(team)
    db.commit()
    bad = [
        {"kind": "global", "team_id": team.id},
        {"kind": "team"},
        {"kind": "host", "team_id": team.id},
        {"kind": "team", "team_id": team.id, "host_name": "web1"},
    ]
    for i, fields in enumerate(bad):
        db.add(Token(name="t", public_id=f"0000000{i}", secret_hash="0" * 64, created_at=NOW,
                     created_by="test", **fields))  # fmt: skip
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
