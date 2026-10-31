"""Upload tokens: shown once, stored hashed, checked in constant time."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from pydebuprep.portal import TOKEN
from sqlalchemy.orm import Session

from pydebuprep_portal.models import Token
from pydebuprep_portal.tokens import (
    TokenError,
    authenticate,
    create_token,
    list_tokens,
    revoke_token,
)

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)


def make(db: Session, **kw: object) -> tuple[Token, str]:
    args: dict[str, object] = {
        "kind": "global",
        "name": "t",
        "actor": "test",
        "expires_at": None,
    }
    return create_token(db, **(args | kw))  # type: ignore[arg-type]


def test_created_token_authenticates(db: Session) -> None:
    token, plain = make(db)
    assert plain.startswith(f"pdp_{token.public_id}_")
    found = authenticate(db, f"Bearer {plain}", NOW)
    assert found.id == token.id
    assert found.last_used_at is None  # recorded when a report is stored, not here


def test_only_the_hash_is_stored(db: Session) -> None:
    token, plain = make(db)
    secret = plain.split("_", 2)[2]
    row = db.get(Token, token.id)
    assert row is not None
    for column in Token.__table__.columns:
        assert secret not in str(getattr(row, column.key))


def test_tokens_are_different(db: Session) -> None:
    assert make(db)[1] != make(db)[1]


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic x",
        "Bearer ",
        "Bearer pdp_",
        "Bearer nope",
        "bearer pdp_00000000_x",
        "Bearer pdp_00000000_" + "x" * 43,
        "Bearer pdp_0000000g_" + "x" * 43,
    ],
)
def test_bad_headers_are_refused(db: Session, header: str | None) -> None:
    make(db)
    with pytest.raises(TokenError):
        authenticate(db, header, NOW)


def test_wrong_secret_is_refused_without_echoing_it(db: Session) -> None:
    token, _ = make(db)
    wrong = "y" * 43
    with pytest.raises(TokenError) as exc:
        authenticate(db, f"Bearer pdp_{token.public_id}_{wrong}", NOW)
    assert wrong not in str(exc.value)


def test_revoked_token_is_refused(db: Session) -> None:
    token, plain = make(db)
    assert revoke_token(db, token.id, NOW, "test") is True
    with pytest.raises(TokenError, match="revoked"):
        authenticate(db, f"Bearer {plain}", NOW)


def test_revoking_twice_or_unknown(db: Session) -> None:
    token, _ = make(db)
    assert revoke_token(db, token.id, NOW, "test")
    assert revoke_token(db, token.id, NOW + timedelta(days=1), "test")
    assert db.get(Token, token.id).revoked_at == NOW  # type: ignore[union-attr]
    assert revoke_token(db, 99999, NOW, "test") is False


def test_expired_token_is_refused(db: Session) -> None:
    _, plain = make(db, expires_at=NOW)
    with pytest.raises(TokenError, match="expired"):
        authenticate(db, f"Bearer {plain}", NOW)
    _, plain = make(db, expires_at=NOW + timedelta(seconds=1))
    assert authenticate(db, f"Bearer {plain}", NOW)


def test_token_matches_the_cli_pattern(db: Session) -> None:
    _, plain = make(db)
    assert re.fullmatch(r"[A-Za-z0-9._~+/=-]+", plain)
    assert TOKEN.fullmatch(plain)


def test_list_tokens_newest_first(db: Session) -> None:
    a, _ = make(db, name="a")
    b, _ = make(db, name="b")
    assert [t.id for t in list_tokens(db)] == [b.id, a.id]


def test_unknown_kind_is_refused(db: Session) -> None:
    with pytest.raises(ValueError, match="kind"):
        make(db, kind="other")
