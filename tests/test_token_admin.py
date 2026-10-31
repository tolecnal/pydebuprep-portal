"""Admins make and revoke global upload tokens; auditors see them; nobody sees a secret twice."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import csrf_of
from pydebuprep_portal.models import Token
from sample import sample_json

Bearer = Callable[..., dict[str, str]]
TOKEN = re.compile(r"pdp_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


def create(client: TestClient, **form: str) -> str:
    resp = client.post("/tokens", data={"csrf_token": csrf_of(client)} | form)
    assert resp.status_code == 200, resp.text
    match = TOKEN.search(resp.text)
    assert match, "the new token is shown"
    return match.group(0)


def upload(client: TestClient, token: str) -> int:
    return client.post(
        "/api/v1/reports",
        content=sample_json().encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    ).status_code


def test_admin_creates_and_sees_token_once(admin: TestClient, db: Session) -> None:
    resp = admin.post("/tokens", data={"csrf_token": csrf_of(admin), "name": "web hosts"})
    assert resp.headers["Cache-Control"] == "no-store"
    plain = TOKEN.search(resp.text).group(0)  # type: ignore[union-attr]
    listing = admin.get("/tokens").text
    assert "web hosts" in listing
    assert plain not in listing
    token = db.query(Token).one()
    assert token.secret_hash not in listing and token.secret_hash not in resp.text
    assert token.created_by == "Ada Admin <user-1>"
    assert token.expires_at is None


def test_expiry_from_the_form(admin: TestClient, db: Session) -> None:
    create(admin, name="short", expires_days="30")
    assert db.query(Token).one().expires_at is not None


def test_created_token_uploads(admin: TestClient) -> None:
    assert upload(admin, create(admin, name="t")) == 201


def test_revoked_token_stops_uploading(admin: TestClient, db: Session) -> None:
    plain = create(admin, name="t")
    token_id = db.query(Token).one().id
    resp = admin.post(f"/tokens/{token_id}/revoke", data={"csrf_token": csrf_of(admin)},
                      follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, "/tokens")
    assert upload(admin, plain) == 401
    assert "revoked" in admin.get("/tokens").text


def test_revoking_an_unknown_token_is_404(admin: TestClient) -> None:
    assert (
        admin.post("/tokens/99999/revoke", data={"csrf_token": csrf_of(admin)}).status_code == 404
    )


def test_auditor_reads_but_cannot_change(
    signed_in: Callable[..., TestClient], admin: TestClient, db: Session
) -> None:
    create(admin, name="seen by auditors")
    auditor = signed_in("pydebuprep-auditors")
    page = auditor.get("/tokens").text
    assert "seen by auditors" in page
    assert 'action="/tokens"' not in page  # no form to create
    token = db.query(Token).one()
    assert (
        auditor.post("/tokens", data={"csrf_token": csrf_of(auditor), "name": "x"}).status_code
        == 403
    )
    resp = auditor.post(f"/tokens/{token.id}/revoke", data={"csrf_token": csrf_of(auditor)})
    assert resp.status_code == 403


def test_member_cannot_see_tokens(signed_in: Callable[..., TestClient]) -> None:
    member = signed_in("pydebuprep-team-web")
    assert member.get("/tokens").status_code == 403
    assert 'href="/tokens"' not in member.get("/").text


def test_token_forms_need_csrf(admin: TestClient, db: Session) -> None:
    assert admin.post("/tokens", data={"name": "x"}).status_code == 403
    create(admin, name="t")
    token = db.query(Token).one()
    assert admin.post(f"/tokens/{token.id}/revoke").status_code == 403
    assert token.revoked_at is None


@pytest.mark.parametrize(
    "form",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "x", "expires_days": "0"},
        {"name": "x", "expires_days": "3651"},
        {"name": "x", "expires_days": "soon"},
    ],
)
def test_bad_token_name_or_expiry_on_the_page(
    admin: TestClient, db: Session, form: dict[str, str]
) -> None:
    resp = admin.post("/tokens", data={"csrf_token": csrf_of(admin)} | form)
    assert resp.status_code == 422
    assert 'class="notice"' in resp.text
    assert db.query(Token).count() == 0


def test_api_token_admin(admin: TestClient, bearer: Bearer, db: Session) -> None:
    headers = bearer("pydebuprep-admins")
    resp = admin.post("/api/v1/tokens", json={"name": "api", "expires_days": 7}, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert set(data) == {"id", "token"} and TOKEN.fullmatch(data["token"])
    listed = admin.get("/api/v1/tokens", headers=headers).json()["tokens"]
    assert [t["name"] for t in listed] == ["api"]
    assert set(listed[0]) == {"id", "kind", "name", "public_id", "team", "host_name", "created_by", "created_at",
                              "expires_at", "revoked_at", "last_used_at"}  # fmt: skip
    hashed = db.query(Token).one().secret_hash
    assert hashed not in resp.text and hashed not in str(listed)
    assert admin.delete(f"/api/v1/tokens/{data['id']}", headers=headers).status_code == 204
    assert admin.delete("/api/v1/tokens/99999", headers=headers).status_code == 404
    assert upload(admin, data["token"]) == 401


def test_api_token_roles(admin: TestClient, bearer: Bearer) -> None:
    auditor, member = bearer("pydebuprep-auditors"), bearer()
    assert admin.get("/api/v1/tokens", headers=auditor).status_code == 200
    assert admin.post("/api/v1/tokens", json={"name": "x"}, headers=auditor).status_code == 403
    assert admin.delete("/api/v1/tokens/1", headers=auditor).status_code == 403
    assert admin.get("/api/v1/tokens", headers=member).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "x", "expires_days": 0},
        {"name": "x", "expires_days": 3651},
        {},
    ],
)
def test_api_bad_token_requests(admin: TestClient, bearer: Bearer, body: dict[str, object]) -> None:
    resp = admin.post("/api/v1/tokens", json=body, headers=bearer("pydebuprep-admins"))
    assert resp.status_code == 422
