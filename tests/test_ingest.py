"""store_report when a concurrent upload gets there first."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pydebuprep_portal import ingest
from pydebuprep_portal.db import make_sessionmaker
from pydebuprep_portal.ingest import IngestError, store_report
from pydebuprep_portal.models import Report, Token
from pydebuprep_portal.tokens import create_token
from sample import sample_json

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)


def a_token(db: Session) -> Token:
    return create_token(db, kind="global", name="t", created_by="test", expires_at=None)[0]


def test_same_report_stored_meanwhile(
    db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = a_token(db)
    body = sample_json().encode()
    original = ingest._insert
    calls = 0

    def racing(*args: Any, **kw: Any) -> Report:
        nonlocal calls
        calls += 1
        if calls == 1:  # another upload of the same report commits first
            with make_sessionmaker(engine)() as other:
                original(other, **(kw | {"token_id": token.id}))
        return original(*args, **kw)

    monkeypatch.setattr(ingest, "_insert", racing)
    _, created = store_report(db, body, token.id, NOW)
    assert created is False
    assert db.query(Report).count() == 1


def test_gives_up_after_two_clashes(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def clash(*args: Any, **kw: Any) -> Report:
        raise IntegrityError("insert", {}, Exception("clash"))

    monkeypatch.setattr(ingest, "_insert", clash)
    with pytest.raises(IngestError) as exc:
        store_report(db, sample_json().encode(), a_token(db).id, NOW)
    assert exc.value.status == 503
