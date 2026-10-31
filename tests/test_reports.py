"""The newest reports, a report, and its downloads — pages and API."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydebuprep.render import FORMATS, render
from pydebuprep.reportfile import load_report

from pydebuprep_portal.models import Report
from pydebuprep_portal.rendering import REPORT_CSP, download_name
from sample import sample_json

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]


def ids_on(page: str) -> list[int]:
    return [int(i) for i in re.findall(r'href="/reports/(\d+)"', page)]


def test_admin_and_auditor_see_reports_newest_first(
    signed_in: Callable[..., TestClient], add_report: AddReport
) -> None:
    a = add_report(hostname="web1")
    b = add_report(hostname="web2")
    for group in ("pydebuprep-admins", "pydebuprep-auditors"):
        page = signed_in(group).get("/").text
        assert ids_on(page) == [b.id, a.id]
        assert "web2" in page and "critical" in page


def test_member_without_roles_sees_none(
    signed_in: Callable[..., TestClient], add_report: AddReport, bearer: Bearer
) -> None:
    report = add_report()
    member = signed_in("pydebuprep-team-web")
    page = member.get("/").text
    assert ids_on(page) == []
    assert "No reports to show" in page
    assert member.get(f"/reports/{report.id}").status_code == 404
    assert member.get(f"/reports/{report.id}/html").status_code == 404
    assert member.get(f"/reports/{report.id}/download/json").status_code == 404
    headers = bearer()
    assert member.get("/api/v1/reports", headers=headers).json()["reports"] == []
    assert member.get(f"/api/v1/reports/{report.id}", headers=headers).status_code == 404
    assert member.get(f"/api/v1/reports/{report.id}/json", headers=headers).status_code == 404


def test_filter_by_worst_and_pagination(
    admin: TestClient, add_report: AddReport, bearer: Bearer
) -> None:
    stored = [add_report(generated_at=f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00+00:00",
                         hostname=f"h{i}") for i in range(120)]  # fmt: skip
    newest_first = [r.id for r in reversed(stored)]
    headers = bearer("pydebuprep-admins")
    seen: list[int] = []
    before = None
    for size in (50, 50, 20):
        params = {"before": before} if before else {}
        data = admin.get("/api/v1/reports", params=params, headers=headers).json()
        assert len(data["reports"]) == size
        seen += [r["id"] for r in data["reports"]]
        before = data["next_before"]
    assert before is None
    assert seen == newest_first
    page = admin.get("/").text
    assert ids_on(page) == newest_first[:50]
    assert f'href="/?before={newest_first[49]}"' in page
    ok = add_report(findings=())
    assert ids_on(admin.get("/", params={"worst": "ok"}).text) == [ok.id]
    data = admin.get("/api/v1/reports", params={"worst": "ok"}, headers=headers).json()
    assert [r["id"] for r in data["reports"]] == [ok.id]
    assert admin.get("/api/v1/reports", params={"worst": "bad"}, headers=headers).status_code == 422


def test_htmx_request_gets_rows_only(admin: TestClient, add_report: AddReport) -> None:
    report = add_report()
    resp = admin.get("/", headers={"HX-Request": "true"})
    assert "<html" not in resp.text
    assert ids_on(resp.text) == [report.id]


def test_report_page(admin: TestClient, add_report: AddReport) -> None:
    report = add_report(hostname="web1")
    page = admin.get(f"/reports/{report.id}").text
    assert "web1" in page and "debian 13 (trixie)" in page and "6.12.0-1-amd64" in page
    assert "fixture" in page  # the token that sent it
    assert f'<iframe sandbox="" src="/reports/{report.id}/html"' in page
    for fmt in FORMATS:
        assert f'href="/reports/{report.id}/download/{fmt}"' in page


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_downloads(admin: TestClient, add_report: AddReport, fmt: str) -> None:
    report = add_report(hostname="web1")
    resp = admin.get(f"/reports/{report.id}/download/{fmt}")
    assert resp.status_code == 200
    name = download_name("web1", report.generated_at, fmt)
    assert resp.headers["Content-Disposition"] == f'attachment; filename="{name}"'
    assert resp.text == render(load_report(sample_json(hostname="web1")), fmt)


@pytest.mark.parametrize("fmt", FORMATS)
def test_api_renders_every_format(
    admin: TestClient, add_report: AddReport, bearer: Bearer, fmt: str
) -> None:
    report = add_report()
    resp = admin.get(f"/api/v1/reports/{report.id}/{fmt}", headers=bearer("pydebuprep-auditors"))
    assert resp.status_code == 200
    assert resp.text == render(load_report(sample_json()), fmt)


def test_api_report_metadata(admin: TestClient, add_report: AddReport, bearer: Bearer) -> None:
    report = add_report(hostname="web1", stale=True)
    data = admin.get(f"/api/v1/reports/{report.id}", headers=bearer("pydebuprep-admins")).json()
    assert data["id"] == report.id
    assert data["host"] == "web1"
    assert data["worst"] == "critical"
    assert (data["critical"], data["warning"], data["info"], data["error"]) == (1, 1, 0, 0)
    assert data["config_stale"] is True
    assert data["token"] == "fixture"
    assert data["formats"] == list(FORMATS)


def test_unknown_report_or_format_is_404(
    admin: TestClient, add_report: AddReport, bearer: Bearer
) -> None:
    report = add_report()
    assert admin.get(f"/reports/{report.id}/download/pdf").status_code == 404
    assert admin.get("/reports/999999").status_code == 404
    assert (
        admin.get(
            f"/api/v1/reports/{report.id}/pdf", headers=bearer("pydebuprep-admins")
        ).status_code
        == 404
    )


def test_report_html_is_sandboxed(admin: TestClient, add_report: AddReport) -> None:
    report = add_report()
    resp = admin.get(f"/reports/{report.id}/html")
    assert resp.headers["Content-Security-Policy"] == REPORT_CSP
    assert "sandbox" in REPORT_CSP and "default-src 'none'" in REPORT_CSP
    assert "frame-ancestors 'self'" in REPORT_CSP
    assert resp.headers["Content-Type"].startswith("text/html")


def test_hostile_report_text_is_escaped(admin: TestClient, add_report: AddReport) -> None:
    evil = "<script>alert(1)</script></iframe>"
    report = add_report(findings=(("x", "warning", evil),))
    for path in (f"/reports/{report.id}", f"/reports/{report.id}/html", "/"):
        assert evil not in admin.get(path).text


def test_stale_config_badge(admin: TestClient, add_report: AddReport) -> None:
    stale = add_report(stale=True)
    fresh = add_report(hostname="web2")
    assert "may not be the latest" in admin.get(f"/reports/{stale.id}").text
    assert "may not be the latest" not in admin.get(f"/reports/{fresh.id}").text


def test_a_token_does_not_read_reports(api: TestClient, token: str, add_report: AddReport) -> None:
    report = add_report()
    headers = {"Authorization": f"Bearer {token}"}
    for path in (
        "/api/v1/reports",
        f"/api/v1/reports/{report.id}",
        f"/api/v1/reports/{report.id}/json",
    ):
        assert api.get(path, headers=headers).status_code == 401


def test_pages_need_sign_in(api: TestClient, add_report: AddReport) -> None:
    report = add_report()
    for path in (
        f"/reports/{report.id}",
        f"/reports/{report.id}/html",
        f"/reports/{report.id}/download/json",
    ):
        assert api.get(path, follow_redirects=False).status_code == 303


def test_api_filter_by_host(admin: TestClient, add_report: AddReport, bearer: Bearer) -> None:
    web = add_report(hostname="Web1")
    add_report(hostname="db1")
    data = admin.get(
        "/api/v1/reports", params={"host": "WEB1"}, headers=bearer("pydebuprep-admins")
    ).json()
    assert [r["id"] for r in data["reports"]] == [web.id]
