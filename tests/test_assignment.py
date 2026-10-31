"""Which team an upload belongs to: the host's mapping, the token, and flags."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from conftest import make_team
from pydebuprep_portal.db import make_sessionmaker
from pydebuprep_portal.ingest import IngestError, store_report
from pydebuprep_portal.models import AuditEntry, Host, Report, Token
from pydebuprep_portal.tokens import create_token
from sample import sample_json

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def tokens(db: Session) -> dict[str, Token]:
    web, other = make_team(db, "web"), make_team(db, "db")

    def make(
        kind: str, name: str, team_id: int | None = None, host_name: str | None = None
    ) -> Token:
        return create_token(db, kind=kind, name=name, actor="test", expires_at=None,
                            team_id=team_id, host_name=host_name)[0]  # fmt: skip

    return {
        "global": make("global", "fleet"),
        "web": make("team", "web token", web.id),
        "db": make("team", "db token", other.id),
        "web1": make("host", "web1 only", web.id, "web1"),
    }


def upload(db: Session, token: Token, **kw: object) -> Report:
    return store_report(db, sample_json(**kw).encode(), token.id, NOW)[0]  # type: ignore[arg-type]


def team_of(report: Report) -> str | None:
    return report.team.name if report.team else None


def test_unmapped_host_with_global_token_goes_to_the_queue(
    db: Session, tokens: dict[str, Token]
) -> None:
    report = upload(db, tokens["global"], hostname="q1")
    assert (team_of(report), report.host.team_id, report.token_team_id) == (None, None, None)
    assert db.query(AuditEntry).filter_by(action="host.map").count() == 0


def test_unmapped_host_with_team_token_is_mapped_at_once(
    db: Session, tokens: dict[str, Token]
) -> None:
    report = upload(db, tokens["web"], hostname="w2")
    assert team_of(report) == "web"
    assert report.host.team.name == "web"  # type: ignore[union-attr]
    entry = db.query(AuditEntry).filter_by(action="host.map").one()
    assert (entry.actor, entry.target) == ("token web token", "w2")
    assert entry.details == {"team": "web", "how": "first upload"}


def test_unmapped_host_with_host_token_is_mapped(db: Session, tokens: dict[str, Token]) -> None:
    report = upload(db, tokens["web1"], hostname="WEB1")
    assert (team_of(report), report.host.name) == ("web", "web1")


def test_mapped_host_keeps_its_team_whatever_the_token(
    db: Session, tokens: dict[str, Token]
) -> None:
    upload(db, tokens["web"], hostname="w3")
    report = upload(db, tokens["global"], hostname="w3", generated_at="2026-11-01T09:00:00+00:00")
    assert (team_of(report), report.token_team_id) == ("web", None)


def test_another_teams_token_is_flagged(db: Session, tokens: dict[str, Token]) -> None:
    upload(db, tokens["web"], hostname="w4")
    report = upload(db, tokens["db"], hostname="w4", generated_at="2026-11-01T09:00:00+00:00")
    assert team_of(report) == "web"
    assert report.token_team.name == "db"  # type: ignore[union-attr]
    assert report.host.team.name == "web"  # type: ignore[union-attr]


def test_host_token_for_another_host_is_403(db: Session, tokens: dict[str, Token]) -> None:
    with pytest.raises(IngestError) as exc:
        upload(db, tokens["web1"], hostname="web2")
    assert exc.value.status == 403
    assert "web1" in exc.value.detail
    assert db.query(Report).count() == 0
    assert db.query(Host).count() == 0


def test_host_token_cannot_resend_another_hosts_stored_report(
    db: Session, tokens: dict[str, Token]
) -> None:
    body = sample_json(hostname="web2").encode()
    store_report(db, body, tokens["global"].id, NOW)
    with pytest.raises(IngestError) as exc:
        store_report(db, body, tokens["web1"].id, NOW)
    assert exc.value.status == 403


def test_upload_during_mapping_lands_in_the_team(
    db: Session, engine: Engine, tokens: dict[str, Token]
) -> None:
    first = upload(db, tokens["global"], hostname="m1")
    web_id = make_team(db, "web").id
    stored: list[Report] = []

    def concurrent_upload() -> None:
        with make_sessionmaker(engine)() as other:
            report = store_report(other, sample_json(hostname="m1", generated_at="2026-11-01T09:00:00+00:00").encode(),
                                  tokens["global"].id, NOW)[0]  # fmt: skip
            stored.append(report)

    with engine.connect() as mapping:
        mapping.execute(text("SELECT id FROM hosts WHERE name = 'm1' FOR UPDATE"))
        thread = threading.Thread(target=concurrent_upload)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive(), "the upload must wait for the host's lock"
        mapping.execute(text("UPDATE hosts SET team_id = :t WHERE name = 'm1'"), {"t": web_id})
        mapping.execute(text("UPDATE reports SET team_id = :t WHERE host_id = :h"),
                        {"t": web_id, "h": first.host_id})  # fmt: skip
        mapping.commit()
    thread.join(10)
    db.expire_all()
    assert {r.team_id for r in db.query(Report)} == {web_id}
    assert len(stored) == 1
