"""Upload tokens: `pdp_<public id>_<secret>`, shown once, stored as a SHA-256 hash."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pydebuprep_portal.audit import record
from pydebuprep_portal.ingest import valid_host_name
from pydebuprep_portal.models import TOKEN_KINDS, Team, Token

# A prefix, not a secret.
TOKEN_PREFIX = "pdp_"  # nosec B105
BEARER = re.compile(r"Bearer pdp_([0-9a-f]{8})_([A-Za-z0-9_-]{43})", re.IGNORECASE)
# Compared against when the public id is unknown, so a miss takes as long as a hit.
_NO_HASH = "0" * 64


MAX_NAME = 100
MAX_EXPIRES_DAYS = 3650


def token_request(
    name: str, expires_days: int | None, now: datetime
) -> tuple[str, datetime | None]:
    """A new token's name and expiry, checked; ValueError says what is wrong."""
    name = name.strip()
    if not name:
        raise ValueError("the token needs a name")
    if len(name) > MAX_NAME:
        raise ValueError(f"a token's name may be at most {MAX_NAME} characters")
    if expires_days is None:
        return name, None
    if not 0 < expires_days <= MAX_EXPIRES_DAYS:
        raise ValueError(f"a token expires after 1 to {MAX_EXPIRES_DAYS} days")
    return name, now + timedelta(days=expires_days)


class TokenError(Exception):
    """Why a request's token was refused; never contains the token."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_token(
    session: Session,
    *,
    kind: str,
    name: str,
    actor: str,
    expires_at: datetime | None,
    team_id: int | None = None,
    host_name: str | None = None,
) -> tuple[Token, str]:
    """Store a new token and return it with its plaintext, which is never stored.
    A global token has no team; a team token has a team; a host token has a team and
    the one host name it may upload for. ValueError says what is wrong."""
    if kind not in TOKEN_KINDS:
        raise ValueError(f"unknown token kind {kind!r}")
    if (kind == "global") != (team_id is None):
        raise ValueError("a global token has no team; team and host tokens need one")
    if kind == "host":
        if host_name is None or not valid_host_name(host_name.strip()):
            raise ValueError("a host token needs the host name it is for")
        host_name = host_name.strip().lower()
    elif host_name is not None:
        raise ValueError("only a host token is for one host")
    public_id = secrets.token_hex(4)
    while session.scalar(select(Token.id).where(Token.public_id == public_id)) is not None:
        public_id = secrets.token_hex(4)  # pragma: no cover - a 1 in 4 billion clash
    secret = secrets.token_urlsafe(32)
    token = Token(
        kind=kind,
        name=name,
        public_id=public_id,
        secret_hash=_hash(secret),
        created_at=datetime.now(UTC),
        created_by=actor,
        expires_at=expires_at,
        team_id=team_id,
        host_name=host_name,
    )
    session.add(token)
    session.flush()
    team = None if team_id is None else session.get(Team, team_id)
    record(session, actor, "token.create", name, kind=kind,
           team=team.name if team else None, host_name=host_name, public_id=public_id)  # fmt: skip

    session.commit()
    return token, f"{TOKEN_PREFIX}{public_id}_{secret}"


def authenticate(session: Session, authorization: str | None, now: datetime) -> Token:
    """The token in an `Authorization: Bearer` header, or TokenError. Only reads: it
    takes no lock, so a slow upload can't hold up others sent with the same token."""
    match = BEARER.fullmatch(authorization or "")
    if match is None:
        raise TokenError("expected an upload token: Authorization: Bearer pdp_…")
    public_id, secret = match.groups()
    token = session.scalar(select(Token).where(Token.public_id == public_id))
    stored = token.secret_hash if token is not None else _NO_HASH
    if not hmac.compare_digest(stored, _hash(secret)) or token is None:
        raise TokenError("unknown upload token")
    if token.revoked_at is not None:
        raise TokenError("this upload token has been revoked")
    if token.expires_at is not None and token.expires_at <= now:
        raise TokenError("this upload token has expired")
    return token


def find_token(session: Session, token_id: int, team_id: int | None = None) -> Token | None:
    """The token; with TEAM_ID, only if it is that team's."""
    token = session.get(Token, token_id)
    if token is None or (team_id is not None and token.team_id != team_id):
        return None
    return token


def revoke_token(
    session: Session, token_id: int, now: datetime, actor: str, team_id: int | None = None
) -> bool:
    """Revoke a token (once; revoking again keeps the first time). False if unknown, or
    with TEAM_ID, not that team's."""
    token = find_token(session, token_id, team_id)
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = now
        record(session, actor, "token.revoke", token.name, kind=token.kind,
               team=token.team.name if token.team else None, public_id=token.public_id)  # fmt: skip
        session.commit()
    return True


def list_tokens(session: Session, team_id: int | None = None) -> list[Token]:
    """Every token, newest first; with TEAM_ID, that team's."""
    query = select(Token).options(joinedload(Token.team)).order_by(Token.id.desc())
    if team_id is not None:
        query = query.where(Token.team_id == team_id)
    return list(session.scalars(query))
