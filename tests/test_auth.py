"""Signing in with OIDC, signing out, CSRF and API bearer tokens."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest import csrf_of, login
from oidc_stub import OIDCStub


def test_sign_in_round_trip(oidc_client: TestClient, stub: OIDCStub) -> None:
    resp = login(oidc_client, stub)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    page = oidc_client.get("/")
    assert page.status_code == 200
    assert "Ada Admin" in page.text


def test_name_falls_back_to_username_then_subject(oidc_client: TestClient, stub: OIDCStub) -> None:
    login(oidc_client, stub, name=None, preferred_username="ada")
    assert "ada" in oidc_client.get("/").text
    login(oidc_client, stub, name=None, sub="user-42")
    assert "user-42" in oidc_client.get("/").text


def test_roles_are_read_at_every_login(oidc_client: TestClient, stub: OIDCStub) -> None:
    login(oidc_client, stub)
    assert '<span class="role">admin</span>' in oidc_client.get("/").text
    login(oidc_client, stub, groups=["pydebuprep-auditors"])
    page = oidc_client.get("/").text
    assert '<span class="role">auditor</span>' in page
    assert '<span class="role">admin</span>' not in page


def test_callback_with_wrong_state_is_refused(oidc_client: TestClient, stub: OIDCStub) -> None:
    resp = oidc_client.get("/login", follow_redirects=False)
    nonce = parse_qs(urlsplit(resp.headers["location"]).query)["nonce"][0]
    resp = oidc_client.get(
        "/auth/callback", params={"code": stub.code_for(nonce), "state": "forged"}
    )
    assert resp.status_code == 400
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_replayed_callback_is_refused(oidc_client: TestClient, stub: OIDCStub) -> None:
    resp = oidc_client.get("/login", follow_redirects=False)
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    params = {"code": stub.code_for(query["nonce"][0]), "state": query["state"][0]}
    assert (
        oidc_client.get("/auth/callback", params=params, follow_redirects=False).status_code == 303
    )
    oidc_client.cookies.clear()
    assert oidc_client.get("/auth/callback", params=params).status_code == 400


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"nonce": "wrong"},
        {"exp": 1000},
    ],
)
def test_bad_id_tokens_are_refused(oidc_client: TestClient, stub: OIDCStub, claims: dict) -> None:
    resp = login(oidc_client, stub, **claims)
    assert resp.status_code == 400
    assert "Sign-in failed" in resp.text
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_provider_error_is_shown(oidc_client: TestClient) -> None:
    oidc_client.get("/login", follow_redirects=False)
    resp = oidc_client.get("/auth/callback", params={"error": "access_denied"})
    assert resp.status_code == 400
    assert "Sign-in failed" in resp.text


def test_pages_redirect_to_login(oidc_client: TestClient) -> None:
    resp = oidc_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=%2F"


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example/", "evil", "/\\evil"])
def test_open_redirect_in_next_is_ignored(
    oidc_client: TestClient, stub: OIDCStub, target: str
) -> None:
    assert login(oidc_client, stub, next_path=target).headers["location"] == "/"


def test_next_path_is_kept(oidc_client: TestClient, stub: OIDCStub) -> None:
    assert login(oidc_client, stub, next_path="/hosts?x=1").headers["location"] == "/hosts?x=1"


def test_logout_needs_csrf(oidc_client: TestClient, stub: OIDCStub) -> None:
    login(oidc_client, stub)
    assert oidc_client.post("/logout").status_code == 403
    assert oidc_client.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
    resp = oidc_client.post(
        "/logout", data={"csrf_token": csrf_of(oidc_client)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(f"{stub.issuer}/logout?")
    assert "post_logout_redirect_uri=https%3A%2F%2Fportal.test%2F" in resp.headers["location"]
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_logout_without_end_session_endpoint(oidc_client: TestClient, stub: OIDCStub) -> None:
    stub.end_session = False
    login(oidc_client, stub)
    resp = oidc_client.post(
        "/logout", data={"csrf_token": csrf_of(oidc_client)}, follow_redirects=False
    )
    assert resp.headers["location"] == "/"


def test_session_cookie_flags(oidc_client: TestClient, stub: OIDCStub) -> None:
    cookie = login(oidc_client, stub).headers["set-cookie"].lower()
    assert "portal_session=" in cookie
    for flag in ("secure", "httponly", "samesite=lax"):
        assert flag in cookie


def test_session_cookie_is_not_secure_with_allow_http(make_client, stub: OIDCStub) -> None:  # type: ignore[no-untyped-def]
    client = make_client(PORTAL_OIDC_ISSUER=stub.issuer, PORTAL_ALLOW_HTTP="1")
    assert "secure" not in login(client, stub).headers["set-cookie"].lower()


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "expired",
        "wrong-aud",
        "wrong-iss",
        "bad-signature",
        "upload-token",
        "none",
        "garbage",
    ],
)
def test_api_bearer(oidc_client: TestClient, stub: OIDCStub, case: str) -> None:
    tokens = {
        "valid": stub.access_token(),
        "expired": stub.access_token(exp=1000),
        "wrong-aud": stub.access_token(aud="other"),
        "wrong-iss": stub.access_token(iss="https://evil.example"),
        "bad-signature": stub.sign(stub.claims(), stub.other_key),
        "upload-token": "pdp_0123abcd_" + "x" * 43,
        "garbage": "a.b.c",
    }
    headers = {"Authorization": f"Bearer {tokens[case]}"} if case in tokens else {}
    resp = oidc_client.get("/api/v1/whoami", headers=headers)
    if case == "valid":
        assert resp.json() == {
            "subject": "user-1",
            "name": "Ada Admin",
            "roles": ["admin"],
            "teams": [],
            "leads": [],
        }
    else:
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_api_audience_list_and_custom_audience(make_client, stub: OIDCStub) -> None:  # type: ignore[no-untyped-def]
    client = make_client(PORTAL_OIDC_ISSUER=stub.issuer, PORTAL_OIDC_AUDIENCE="portal-api")
    token = stub.access_token(aud=["account", "portal-api"])
    assert (
        client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_api_keys_refetched_once_for_a_new_key(oidc_client: TestClient, stub: OIDCStub) -> None:
    headers = {"Authorization": f"Bearer {stub.access_token()}"}
    assert oidc_client.get("/api/v1/whoami", headers=headers).status_code == 200
    before = stub.jwks_fetches
    stub.key = stub.new_key  # the provider rotated its key
    headers = {"Authorization": f"Bearer {stub.access_token()}"}
    assert oidc_client.get("/api/v1/whoami", headers=headers).status_code == 200
    assert stub.jwks_fetches == before + 1


def test_api_unknown_keys_dont_hammer_the_provider(oidc_client: TestClient, stub: OIDCStub) -> None:
    good = {"Authorization": f"Bearer {stub.access_token()}"}
    oidc_client.get("/api/v1/whoami", headers=good)
    for key in (stub.other_key, stub.stranger_key):
        forged = {"Authorization": f"Bearer {stub.sign(stub.claims(), key)}"}
        for _ in range(5):
            assert oidc_client.get("/api/v1/whoami", headers=forged).status_code == 401
    assert stub.jwks_fetches == 2  # the first fetch, and one more for the unknown key id


def test_provider_down_is_503(make_client) -> None:  # type: ignore[no-untyped-def]
    client = make_client(PORTAL_OIDC_ISSUER="http://127.0.0.1:9/realms/none")
    assert client.get("/login").status_code == 503
    resp = client.get("/api/v1/whoami", headers={"Authorization": "Bearer a.b.c"})
    assert resp.status_code in (401, 503)


def test_csrf_token_stays_the_same_within_a_session(
    oidc_client: TestClient, stub: OIDCStub
) -> None:
    login(oidc_client, stub)
    assert csrf_of(oidc_client) == csrf_of(oidc_client)


def test_logout_when_the_provider_is_down(oidc_client: TestClient, stub: OIDCStub) -> None:
    login(oidc_client, stub)
    token = csrf_of(oidc_client)
    oidc_client.app.state.oauth.oidc.server_metadata = {}  # not fetched yet
    stub.close()
    resp = oidc_client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.headers["location"] == "/"
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_an_id_token_is_not_an_api_token(oidc_client: TestClient, stub: OIDCStub) -> None:
    token = stub.access_token(typ="ID")
    assert (
        oidc_client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
    token = stub.access_token(typ="Bearer")
    assert (
        oidc_client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_metadata_from_an_internal_url(make_client, stub: OIDCStub) -> None:  # type: ignore[no-untyped-def]
    # The provider's public issuer isn't reachable from here; its metadata URL is.
    client = make_client(
        PORTAL_OIDC_ISSUER="https://login.example.invalid/realms/test",
        PORTAL_OIDC_METADATA_URL=f"{stub.issuer}/.well-known/openid-configuration",
    )
    assert client.get("/login", follow_redirects=False).status_code == 302
