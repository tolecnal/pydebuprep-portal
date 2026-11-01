"""After a form, pages redirect to what is stored, never to what the request named."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import csrf_of, make_team

AddReport = Callable[..., object]


def _post(client: TestClient, path: str, **data: str) -> tuple[int, str]:
    resp = client.post(path, data={"csrf_token": csrf_of(client), **data}, follow_redirects=False)
    return resp.status_code, resp.headers.get("location", "")


def test_host_forms_redirect_to_the_stored_host(
    signed_in: Callable[..., TestClient], db: Session, add_report: AddReport
) -> None:
    make_team(db, "web")
    add_report(hostname="q1")
    admin = signed_in("pydebuprep-admins")
    assert _post(admin, "/hosts/Q1/map", team="web") == (303, "/hosts/q1")
    assert _post(admin, "/hosts/Q1/unmap") == (303, "/hosts/q1")
    assert _post(admin, "/queue/Q1/map", team="web") == (303, "/queue")


def test_config_forms_redirect_to_the_stored_config(
    signed_in: Callable[..., TestClient], db: Session
) -> None:
    make_team(db, "web")
    member = signed_in("pydebuprep-team-web")
    assert _post(member, "/configs", team="web", name=" default ") == (303, "/configs/web/default")
    todo = {"_fields": "report.todo", "report.todo": "1"}
    assert _post(member, "/configs/web/default", **todo) == (303, "/configs/web/default")
    saved = _post(member, "/configs/web/default", **todo)
    assert saved == (303, "/configs/web/default?unchanged=1")
