"""Upload tokens: `pdp_<public id>_<secret>`, shown once, stored as a SHA-256 hash."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pydebuprep_portal.models import TOKEN_KINDS, Token

# A prefix, not a secret.
TOKEN_PREFIX = "pdp_"  # nosec B105
BEARER = re.compile(r"Bearer pdp_([0-9a-f]{8})_([A-Za-z0-9_-]{43})", re.IGNORECASE)
# Compared against when the public id is unknown, so a miss takes as long as a hit.
_NO_HASH = "0" * 64


class TokenError(Exception):
    """Why a request's token was refused; never contains the token."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_token(
    session: Session, *, kind: str, name: str, created_by: str, expires_at: datetime | None
) -> tuple[Token, str]:
    """Store a new token and return it with its plaintext, which is never stored."""
    if kind not in TOKEN_KINDS:
        raise ValueError(f"unknown token kind {kind!r}")
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
        created_by=created_by,
        expires_at=expires_at,
    )
    session.add(token)
    session.commit()
    return token, f"{TOKEN_PREFIX}{public_id}_{secret}"


def authenticate(session: Session, authorization: str | None, now: datetime) -> Token:
    """The token in an `Authorization: Bearer` header, or TokenError."""
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
    token.last_used_at = now
    session.flush()
    return token


def revoke_token(session: Session, token_id: int, now: datetime) -> bool:
    """Revoke a token (once; revoking again keeps the first time). False if unknown."""
    token = session.get(Token, token_id)
    if token is None:
        return False
    if token.revoked_at is None:
        token.revoked_at = now
        session.commit()
    return True


def list_tokens(session: Session) -> list[Token]:
    return list(session.scalars(select(Token).order_by(Token.id.desc())))
