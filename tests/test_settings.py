"""The portal's settings come from environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import BASE_ENV
from pydebuprep_portal.settings import SettingsError, load_settings


def test_minimal_env_loads() -> None:
    s = load_settings(BASE_ENV)
    assert s.database_url == BASE_ENV["PORTAL_DATABASE_URL"]
    assert s.base_url == "https://portal.test"
    assert s.oidc_groups_claim == "groups"
    assert s.group_prefix == "pydebuprep-"
    assert not hasattr(s, "max_upload_bytes")  # an admin setting since step 3
    assert s.oidc_audience == "portal"
    assert s.allow_http is False


def test_optional_settings() -> None:
    s = load_settings(
        BASE_ENV
        | {
            "PORTAL_OIDC_GROUPS_CLAIM": "roles",
            "PORTAL_OIDC_AUDIENCE": "api",
            "PORTAL_GROUP_PREFIX": "pdp-",
        }
    )
    assert (s.oidc_groups_claim, s.oidc_audience, s.group_prefix) == ("roles", "api", "pdp-")


@pytest.mark.parametrize("name", [n for n in BASE_ENV])
def test_missing_variable_is_named(name: str) -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != name}
    with pytest.raises(SettingsError, match=name):
        load_settings(env)


def test_empty_variable_counts_as_missing() -> None:
    with pytest.raises(SettingsError, match="PORTAL_OIDC_ISSUER"):
        load_settings(BASE_ENV | {"PORTAL_OIDC_ISSUER": ""})


def test_secret_from_file(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("f" * 40 + "\n")
    env = {k: v for k, v in BASE_ENV.items() if k != "PORTAL_SESSION_SECRET"}
    s = load_settings(env | {"PORTAL_SESSION_SECRET_FILE": str(secret)})
    assert s.session_secret == "f" * 40


def test_unreadable_secret_file(tmp_path: Path) -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != "PORTAL_OIDC_CLIENT_SECRET"}
    with pytest.raises(SettingsError, match="PORTAL_OIDC_CLIENT_SECRET_FILE"):
        load_settings(env | {"PORTAL_OIDC_CLIENT_SECRET_FILE": str(tmp_path / "nope")})


def test_both_value_and_file_is_an_error(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("f" * 40)
    with pytest.raises(SettingsError, match="PORTAL_SESSION_SECRET"):
        load_settings(BASE_ENV | {"PORTAL_SESSION_SECRET_FILE": str(secret)})


def test_short_session_secret_rejected() -> None:
    with pytest.raises(SettingsError, match="at least 32"):
        load_settings(BASE_ENV | {"PORTAL_SESSION_SECRET": "short"})


def test_base_url_must_be_https_unless_allow_http() -> None:
    with pytest.raises(SettingsError, match="PORTAL_BASE_URL"):
        load_settings(BASE_ENV | {"PORTAL_BASE_URL": "http://portal.test"})
    s = load_settings(
        BASE_ENV | {"PORTAL_BASE_URL": "http://portal.test/", "PORTAL_ALLOW_HTTP": "1"}
    )
    assert s.base_url == "http://portal.test"
    assert s.allow_http is True


@pytest.mark.parametrize("url", ["portal.test", "https://", "ftp://portal.test"])
def test_base_url_must_be_a_url(url: str) -> None:
    with pytest.raises(SettingsError, match="PORTAL_BASE_URL"):
        load_settings(BASE_ENV | {"PORTAL_BASE_URL": url})


@pytest.mark.parametrize(
    ("value", "expected"), [("1", True), ("true", True), ("0", False), ("no", False)]
)
def test_allow_http_values(value: str, expected: bool) -> None:
    assert load_settings(BASE_ENV | {"PORTAL_ALLOW_HTTP": value}).allow_http is expected


def test_bad_allow_http_value() -> None:
    with pytest.raises(SettingsError, match="PORTAL_ALLOW_HTTP"):
        load_settings(BASE_ENV | {"PORTAL_ALLOW_HTTP": "maybe"})


def test_metadata_url_defaults_to_the_issuers() -> None:
    s = load_settings(BASE_ENV)
    assert s.oidc_metadata_url == "https://idp.test/realms/r/.well-known/openid-configuration"
    internal = "http://keycloak:8080/realms/r/.well-known/openid-configuration"
    assert (
        load_settings(BASE_ENV | {"PORTAL_OIDC_METADATA_URL": internal}).oidc_metadata_url
        == internal
    )
