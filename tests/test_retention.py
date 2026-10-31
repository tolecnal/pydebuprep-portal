"""Retention: a global TTL with team and host overrides, expired daily; never a host's newest."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from conftest import assign, make_team
from pydebuprep_portal.db import make_sessionmaker
from pydebuprep_portal.manage import main
from pydebuprep_portal.models import AuditEntry, Finding, Host, Report, Team
from pydebuprep_portal.retention import (
    Retention,
    describe,
    effective_retention,
    expire_reports,
    run_expiry_forever,
)
from pydebuprep_portal.settings_store import GlobalSettings, get_settings, update_settings

AddReport = Callable[..., Report]
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def aged(db: Session, report: Report, days: int) -> int:
    """Make REPORT received DAYS ago; its id."""
    report.received_at = NOW - timedelta(days=days)
    db.commit()
    return report.id


def gen(n: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=n)).isoformat()


def global_retention(db: Session, days: int | None) -> None:
    s = get_settings(db)
    update_settings(db, "test", retention_days=days, only_admins_map=s.only_admins_map,
                    max_upload_bytes=s.max_upload_bytes)  # fmt: skip


def remaining(db: Session) -> set[int]:
    db.expire_all()
    return {r.id for r in db.query(Report)}


def test_global_ttl_expires_old_reports(db: Session, add_report: AddReport) -> None:
    old = aged(db, add_report(hostname="a", generated_at=gen(1)), 400)
    new = aged(db, add_report(hostname="a", generated_at=gen(2)), 1)
    assert expire_reports(db, NOW) == 1
    assert remaining(db) == {new}
    assert db.query(Finding).filter_by(report_id=old).count() == 0


def test_newest_report_of_a_host_is_never_expired(db: Session, add_report: AddReport) -> None:
    only = aged(db, add_report(hostname="a"), 1000)
    assert expire_reports(db, NOW) == 0
    assert remaining(db) == {only}


def test_newest_is_by_generated_time(db: Session, add_report: AddReport) -> None:
    newest = aged(db, add_report(hostname="a", generated_at=gen(5)), 500)  # sent long ago
    late = aged(db, add_report(hostname="a", generated_at=gen(1)), 400)  # older, sent later
    assert expire_reports(db, NOW) == 1
    assert remaining(db) == {newest}
    assert late not in remaining(db)


def test_host_override_beats_team_beats_global(db: Session, add_report: AddReport) -> None:
    team = make_team(db, "web")
    team.retention_days = 30
    db.commit()
    a_old = aged(db, assign(db, add_report(hostname="a", generated_at=gen(1)), team), 100)
    aged(db, assign(db, add_report(hostname="a", generated_at=gen(2)), team), 1)
    b_old = aged(db, assign(db, add_report(hostname="b", generated_at=gen(1)), team), 100)
    aged(db, assign(db, add_report(hostname="b", generated_at=gen(2)), team), 1)
    c_old = aged(db, assign(db, add_report(hostname="c", generated_at=gen(1)), team), 100)
    aged(db, assign(db, add_report(hostname="c", generated_at=gen(2)), team), 1)
    hosts = {h.name: h for h in db.query(Host)}
    hosts["a"].retention_days = 400  # host longer than team
    hosts["b"].keep_forever = True  # host forever beats team 30
    db.commit()
    assert expire_reports(db, NOW) == 1
    left = remaining(db)
    assert a_old in left and b_old in left and c_old not in left
    team.retention_days = None
    team.keep_forever = True
    hosts["a"].retention_days = 50  # host shorter than a forever team
    db.commit()
    assert expire_reports(db, NOW) == 1
    assert a_old not in remaining(db)


def test_queued_hosts_use_host_then_global(db: Session, add_report: AddReport) -> None:
    q_old = aged(db, add_report(hostname="q", generated_at=gen(1)), 200)
    aged(db, add_report(hostname="q", generated_at=gen(2)), 1)
    global_retention(db, 100)
    host = db.query(Host).one()
    host.retention_days = 300
    db.commit()
    assert expire_reports(db, NOW) == 0
    host.retention_days = None
    db.commit()
    assert expire_reports(db, NOW) == 1
    assert q_old not in remaining(db)


def test_global_keep_forever(db: Session, add_report: AddReport) -> None:
    aged(db, add_report(hostname="a", generated_at=gen(1)), 5000)
    aged(db, add_report(hostname="a", generated_at=gen(2)), 1)
    global_retention(db, None)
    assert expire_reports(db, NOW) == 0


def test_expiry_is_audited_only_when_it_deletes(db: Session, add_report: AddReport) -> None:
    aged(db, add_report(hostname="a", generated_at=gen(1)), 1)
    expire_reports(db, NOW)
    assert db.query(AuditEntry).filter_by(action="retention.expire").count() == 0
    aged(db, add_report(hostname="a", generated_at=gen(0)), 999)
    expire_reports(db, NOW)
    entry = db.query(AuditEntry).filter_by(action="retention.expire").one()
    assert (entry.actor, entry.details) == ("retention", {"deleted": 1})


def test_a_second_runner_waits_for_the_lock(
    db: Session, engine: Engine, add_report: AddReport
) -> None:
    aged(db, add_report(hostname="a", generated_at=gen(1)), 999)
    aged(db, add_report(hostname="a", generated_at=gen(2)), 1)
    with engine.connect() as other:
        other.execute(
            text("SELECT pg_advisory_lock(:k)"),
            {"k": __import__("pydebuprep_portal.retention", fromlist=["LOCK_KEY"]).LOCK_KEY},
        )
        assert expire_reports(db, NOW) == 0
        other.execute(text("SELECT pg_advisory_unlock_all()"))
    assert expire_reports(db, NOW) == 1


def test_run_expiry_forever_runs_and_survives_errors(engine: Engine, db: Session) -> None:
    calls: list[str] = []

    def flaky(session: Session, now: datetime) -> int:
        calls.append("run")
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return 0

    async def run() -> None:
        task = asyncio.create_task(
            run_expiry_forever(
                make_sessionmaker(engine), first_delay=0, interval=0.01, expire=flaky
            )
        )
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) >= 3


def test_manage_expire(
    db: Session, db_env: dict[str, str], add_report: AddReport, capsys: pytest.CaptureFixture[str]
) -> None:
    add_report(hostname="a", generated_at=gen(1)).received_at = datetime.now(UTC) - timedelta(
        days=999
    )
    db.commit()
    add_report(hostname="a", generated_at=gen(2))
    assert main(["expire"]) == 0
    assert capsys.readouterr().out == "Expired 1 reports.\n"


@pytest.mark.parametrize(
    ("host", "team", "glob", "expected"),
    [
        ((None, False), None, 365, Retention(365, "global")),
        ((None, False), (90, False), 365, Retention(90, "team")),
        ((None, False), (None, True), 365, Retention(None, "team")),
        ((10, False), (None, True), 365, Retention(10, "host")),
        ((None, True), (5, False), 365, Retention(None, "host")),
        ((None, False), (None, False), None, Retention(None, "global")),
    ],
)
def test_effective_retention(
    host: tuple, team: tuple | None, glob: int | None, expected: Retention
) -> None:
    h = Host(name="h", retention_days=host[0], keep_forever=host[1])
    t = None if team is None else Team(name="t", retention_days=team[0], keep_forever=team[1])
    settings = GlobalSettings(retention_days=glob, only_admins_map=False, max_upload_bytes=1024)
    assert effective_retention(h, t, settings) == expected


def test_describe() -> None:
    assert describe(Retention(90, "team")) == "90 days (team)"
    assert describe(Retention(None, "host")) == "forever (host)"


def test_run_expiry_forever_logs_deletions(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    def deletes(session: Session, now: datetime) -> int:
        calls.append(1)
        return 3

    async def run() -> None:
        task = asyncio.create_task(
            run_expiry_forever(
                make_sessionmaker(engine), first_delay=0, interval=10, expire=deletes
            )
        )
        while not calls:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level("INFO", logger="pydebuprep_portal.retention"):
        asyncio.run(run())
    assert "expired 3 reports" in caplog.text
