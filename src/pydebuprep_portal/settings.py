"""The portal's settings, read from PORTAL_* environment variables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MIN_SESSION_SECRET = 32
TRUE = frozenset({"1", "true", "yes", "on"})
FALSE = frozenset({"", "0", "false", "no", "off"})


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    base_url: str
    session_secret: str
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_audience: str
    oidc_metadata_url: str = ""  # where the provider's metadata is read; default: the issuer's
    oidc_groups_claim: str = "groups"
    group_prefix: str = "pydebuprep-"
    allow_http: bool = False


def load_settings(env: Mapping[str, str]) -> Settings:
    """Read the settings; SettingsError names the variable that is wrong."""
    allow_http = _bool(env, "PORTAL_ALLOW_HTTP")
    session_secret = _secret(env, "PORTAL_SESSION_SECRET")
    if len(session_secret) < MIN_SESSION_SECRET:
        raise SettingsError(
            f"PORTAL_SESSION_SECRET must be at least {MIN_SESSION_SECRET} characters"
        )
    client_id = _required(env, "PORTAL_OIDC_CLIENT_ID")
    return Settings(
        database_url=_secret(env, "PORTAL_DATABASE_URL"),
        base_url=_base_url(_required(env, "PORTAL_BASE_URL"), allow_http),
        session_secret=session_secret,
        oidc_issuer=_required(env, "PORTAL_OIDC_ISSUER").rstrip("/"),
        oidc_client_id=client_id,
        oidc_client_secret=_secret(env, "PORTAL_OIDC_CLIENT_SECRET"),
        oidc_audience=env.get("PORTAL_OIDC_AUDIENCE") or client_id,
        oidc_metadata_url=env.get("PORTAL_OIDC_METADATA_URL")
        or f"{_required(env, 'PORTAL_OIDC_ISSUER').rstrip('/')}/.well-known/openid-configuration",
        oidc_groups_claim=env.get("PORTAL_OIDC_GROUPS_CLAIM") or "groups",
        group_prefix=env.get("PORTAL_GROUP_PREFIX") or "pydebuprep-",
        allow_http=allow_http,
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SettingsError(f"{name} is not set")
    return value


def _secret(env: Mapping[str, str], name: str) -> str:
    """NAME, or the contents of the file NAME_FILE names (Docker secrets)."""
    file_name = env.get(f"{name}_FILE", "")
    if not file_name:
        return _required(env, name)
    if env.get(name):
        raise SettingsError(f"set {name} or {name}_FILE, not both")
    try:
        return Path(file_name).read_text().rstrip("\n")
    except OSError as exc:
        raise SettingsError(f"{name}_FILE: cannot read {file_name}: {exc.strerror}") from None


def _base_url(value: str, allow_http: bool) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise SettingsError(f"PORTAL_BASE_URL must be an https:// URL, not {value!r}")
    if parts.scheme == "http" and not allow_http:
        raise SettingsError("PORTAL_BASE_URL must use https:// (http:// needs PORTAL_ALLOW_HTTP=1)")
    return value.rstrip("/")


def _bool(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "").strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    raise SettingsError(f"{name} must be 1 or 0, not {value!r}")
