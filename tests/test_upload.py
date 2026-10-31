"""POST /api/v1/reports: hosts upload their JSON reports with a token."""

from __future__ import annotations

import gzip
import json
import tracemalloc
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import set_upload_limit
from pydebuprep_portal.models import Finding, Host, Report
from sample import sample_json

URL = "/api/v1/reports"


def post(client: TestClient, token: str | None, body: bytes, **headers: str) -> object:
    h = {"Content-Type": "application/json"} | headers
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    return client.post(URL, content=body, headers=h)


def test_upload_stores_a_report(api: TestClient, token: str, db: Session) -> None:
    resp = post(api, token, sample_json(hostname="Web1").encode())
    assert resp.status_code == 201
    data = resp.json()
    assert data == {"id": data["id"], "url": f"https://portal.test/reports/{data['id']}"}
    report = db.get(Report, data["id"])
    assert report is not None
    assert report.host.name == "web1"
    assert (report.worst, report.critical, report.warning, report.info, report.error) == (
        "critical", 1, 1, 0, 0,
    )  # fmt: skip
    assert (report.os_id, report.os_version, report.codename, report.tool_version) == (
        "debian", "13", "trixie", "0.5.0",
    )  # fmt: skip
    assert report.token.name == "test"
    assert report.config_stale is False
    assert sorted((f.check_id, f.severity, f.title) for f in report.findings) == [
        ("apt-sources", "critical", "Repository still targets bookworm"),
        ("disk-space", "warning", "Disk is full on /var"),
    ]
    assert report.token.last_used_at is not None


@pytest.mark.parametrize(
    ("findings", "worst"),
    [((), "ok"), ((("x", "info", "i"),), "info"), ((("x", "warning", "w"),), "warning")],
)
def test_worst_severity(
    api: TestClient, token: str, db: Session, findings: tuple, worst: str
) -> None:
    resp = post(api, token, sample_json(findings=findings).encode())
    assert db.get(Report, resp.json()["id"]).worst == worst  # type: ignore[union-attr]


def test_stale_config_is_recorded(api: TestClient, token: str, db: Session) -> None:
    resp = post(api, token, sample_json(stale=True).encode())
    assert db.get(Report, resp.json()["id"]).config_stale is True  # type: ignore[union-attr]


def test_naive_generated_at_is_utc(api: TestClient, token: str, db: Session) -> None:
    resp = post(api, token, sample_json(generated_at="2026-10-31T09:00:00").encode())
    report = db.get(Report, resp.json()["id"])
    assert report is not None and report.generated_at.isoformat() == "2026-10-31T09:00:00+00:00"


def test_gzip_upload(api: TestClient, token: str) -> None:
    resp = post(api, token, gzip.compress(sample_json().encode()), **{"Content-Encoding": "gzip"})
    assert resp.status_code == 201


def test_same_report_twice_is_200_with_the_same_id(
    api: TestClient, token: str, db: Session
) -> None:
    body = sample_json().encode()
    first = post(api, token, body)
    again = post(api, token, gzip.compress(body, mtime=1), **{"Content-Encoding": "gzip"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert db.query(Report).count() == 1


def test_a_second_report_of_a_host_reuses_it(api: TestClient, token: str, db: Session) -> None:
    post(api, token, sample_json().encode())
    post(api, token, sample_json(generated_at="2026-11-01T09:00:00+00:00").encode())
    assert db.query(Host).count() == 1
    assert db.query(Report).count() == 2


@pytest.mark.parametrize("header", [None, "pdp_00000000_" + "x" * 43, "nope"])
def test_missing_or_bad_token_is_401(api: TestClient, db: Session, header: str | None) -> None:
    resp = post(api, header, sample_json().encode())
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert db.query(Report).count() == 0


def test_unknown_schema_version_is_422(api: TestClient, token: str) -> None:
    data = json.loads(sample_json()) | {"schema_version": "2"}
    resp = post(api, token, json.dumps(data).encode())
    assert resp.status_code == 422
    assert "unsupported version" in resp.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [b"\xff", b"[]", b"host: x", b"{", b'{"a":' * 100000, b'{"schema_version": "1"}'],
)
def test_bad_bodies_are_422(api: TestClient, token: str, db: Session, body: bytes) -> None:
    resp = post(api, token, body)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
    assert db.query(Report).count() == 0


@pytest.mark.parametrize("hostname", ["", "   ", "a" * 254, "web 1", "web\x001"])
def test_bad_hostnames_are_422(api: TestClient, token: str, hostname: str) -> None:
    resp = post(api, token, sample_json(hostname=hostname).encode())
    assert resp.status_code == 422
    assert "hostname" in resp.json()["detail"]


def test_wrong_content_type_or_encoding_is_415(api: TestClient, token: str) -> None:
    body = sample_json().encode()
    assert post(api, token, body, **{"Content-Type": "text/plain"}).status_code == 415
    assert post(api, token, body, **{"Content-Encoding": "br"}).status_code == 415
    ok = post(api, token, body, **{"Content-Type": "application/json; charset=utf-8"})
    assert ok.status_code == 201


def test_broken_gzip_is_400(api: TestClient, token: str) -> None:
    gz = gzip.compress(sample_json().encode())
    for body in (b"not gzip", gz[: len(gz) // 2]):
        assert post(api, token, body, **{"Content-Encoding": "gzip"}).status_code == 400


def test_oversize_plain_body_is_413(
    make_client: Callable[..., TestClient], token: str, db: Session
) -> None:
    set_upload_limit(db, 1024)
    client = make_client()
    assert post(client, token, b"{" + b" " * 2000 + b"}").status_code == 413


def test_gzip_bomb_is_413_without_inflating_it(
    make_client: Callable[..., TestClient], token: str, db: Session
) -> None:
    set_upload_limit(db, 1000000)
    client = make_client()
    bomb = gzip.compress(b"{" + b" " * 100_000_000 + b"}")
    tracemalloc.start()
    try:
        resp = post(client, token, bomb, **{"Content-Encoding": "gzip"})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert resp.status_code == 413
    assert peak < 20_000_000


def test_chunked_body_without_length_is_limited(
    make_client: Callable[..., TestClient], token: str, db: Session
) -> None:
    set_upload_limit(db, 1024)
    client = make_client()

    def chunks() -> Iterator[bytes]:
        for _ in range(100):
            yield b" " * 100

    resp = client.post(URL, content=chunks(),
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})  # fmt: skip
    assert resp.status_code == 413


def test_declared_length_over_the_limit_is_413_before_reading(
    make_client: Callable[..., TestClient], token: str, db: Session
) -> None:
    set_upload_limit(db, 1024)
    client = make_client()
    assert (
        post(client, token, b"{" + b" " * 5000 + b"}", **{"Content-Length": "5002"}).status_code
        == 413
    )


def test_reply_is_small(api: TestClient, token: str) -> None:
    assert len(post(api, token, sample_json().encode()).content) <= 4096


def test_findings_include_baseline_changes(api: TestClient, token: str, db: Session) -> None:
    data = json.loads(sample_json(findings=()))
    data["results"][0]["baseline_diff"] = [
        {"severity": "warning", "title": "Unit was running before", "detail": "",
         "evidence": [], "remediation": [], "references": []}
    ]  # fmt: skip
    resp = post(api, token, json.dumps(data).encode())
    report = db.get(Report, resp.json()["id"])
    assert report is not None and report.worst == "warning"
    assert [f.title for f in db.query(Finding)] == ["Unit was running before"]


@pytest.mark.parametrize("where", ["title", "kernel"])
def test_nul_characters_are_422(api: TestClient, token: str, db: Session, where: str) -> None:
    data = json.loads(sample_json())
    if where == "title":
        data["results"][0]["findings"][0]["title"] = "bad\x00title"
    else:
        data["host"]["kernel"] = "6.12\x00"
    resp = post(api, token, json.dumps(data).encode())
    assert resp.status_code == 422
    assert "NUL" in resp.json()["detail"]
    assert db.query(Report).count() == 0


def test_a_repeated_upload_records_the_token_use(api: TestClient, token: str, db: Session) -> None:
    body = sample_json().encode()
    post(api, token, body)
    first = db.query(Report).one().token.last_used_at
    db.expire_all()
    post(api, token, body)
    assert db.query(Report).one().token.last_used_at > first  # type: ignore[operator]
