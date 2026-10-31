"""Searching findings by host, check, severity and words in the title."""

from __future__ import annotations

import html
import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.models import Report
from pydebuprep_portal.queries import search_findings

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
FINDINGS = (
    ("disk-space", "warning", "Disk is full on /var"),
    ("disk-space", "info", "Disk ok"),
    ("apt-sources", "critical", "Repository still targets bookworm"),
    ("apt-sources", "warning", "100% of sources use_http"),
)


def titles(hits: list) -> list[str]:  # type: ignore[type-arg]
    return sorted(h.title for h in hits)


def test_search_by_check_severity_and_words(db: Session, add_report: AddReport) -> None:
    add_report(hostname="web1", findings=FINDINGS)
    add_report(hostname="db1", findings=FINDINGS[:1])
    assert titles(search_findings(db, ADMIN, words="disk FULL")) == ["Disk is full on /var"] * 2
    assert titles(search_findings(db, ADMIN, check="apt-sources")) == [
        "100% of sources use_http", "Repository still targets bookworm"]  # fmt: skip
    assert titles(search_findings(db, ADMIN, severity="critical")) == [
        "Repository still targets bookworm"
    ]
    hits = search_findings(db, ADMIN, host="DB", words="disk")
    assert [(h.host, h.check_id, h.severity) for h in hits] == [("db1", "disk-space", "warning")]


def test_search_words_with_wildcards_are_literal(db: Session, add_report: AddReport) -> None:
    add_report(findings=FINDINGS)
    assert titles(search_findings(db, ADMIN, words="100%")) == ["100% of sources use_http"]
    assert titles(search_findings(db, ADMIN, words="%")) == ["100% of sources use_http"]
    assert titles(search_findings(db, ADMIN, words="use_")) == ["100% of sources use_http"]
    assert search_findings(db, ADMIN, words="_isk") == []


def test_search_newest_first_and_paged(db: Session, add_report: AddReport) -> None:
    for i in range(3):
        add_report(hostname=f"h{i}", findings=FINDINGS[:1])
    hits = search_findings(db, ADMIN, limit=2)
    assert [h.host for h in hits] == ["h2", "h1"]
    assert [h.host for h in search_findings(db, ADMIN, before=hits[-1].id)] == ["h0"]


def test_search_and_hosts_hidden_from_members(
    db: Session, signed_in: Callable[..., TestClient], add_report: AddReport, bearer: Bearer
) -> None:
    add_report(findings=FINDINGS)
    member = signed_in()
    assert search_findings(db, Viewer("m", "m", frozenset())) == []
    assert "Disk is full" not in member.get("/search", params={"words": "disk"}).text
    assert "web1" not in member.get("/hosts").text
    data = member.get("/api/v1/search/findings", params={"words": "disk"}, headers=bearer()).json()
    assert data["findings"] == []


def test_search_page(admin: TestClient, add_report: AddReport) -> None:
    report = add_report(findings=FINDINGS)
    page = admin.get("/search", params={"words": "disk", "severity": "warning"}).text
    assert "Disk is full on /var" in page and "Disk ok" not in page
    assert f'href="/reports/{report.id}"' in page
    assert "Disk is full" not in admin.get("/search").text  # no search without criteria
    assert '<option value="disk-space"' in admin.get("/search").text  # pydebuprep's checks


def test_search_rejects_unknown_severity(
    admin: TestClient, add_report: AddReport, bearer: Bearer
) -> None:
    add_report(findings=FINDINGS)
    resp = admin.get("/api/v1/search/findings", params={"severity": "fatal"},
                     headers=bearer("pydebuprep-admins"))  # fmt: skip
    assert resp.status_code == 422
    page = admin.get("/search", params={"severity": "fatal", "words": "disk"})
    assert page.status_code == 200
    assert "Unknown severity" in page.text
    assert "Disk is full" not in page.text


def test_search_api(admin: TestClient, add_report: AddReport, bearer: Bearer) -> None:
    report = add_report(findings=FINDINGS)
    headers = bearer("pydebuprep-auditors")
    data = admin.get("/api/v1/search/findings", params={"check": "disk-space", "severity": "warning"},
                     headers=headers).json()  # fmt: skip
    assert [(f["report_id"], f["host"], f["title"]) for f in data["findings"]] == [
        (report.id, "web1", "Disk is full on /var")]  # fmt: skip
    assert data["next_before"] is None


@pytest.mark.parametrize("words", ["  ", ""])
def test_blank_words_are_ignored(db: Session, add_report: AddReport, words: str) -> None:
    add_report(findings=FINDINGS)
    assert len(search_findings(db, ADMIN, words=words)) == 4


def test_search_page_links_to_older_findings(admin: TestClient, add_report: AddReport) -> None:
    for i in range(26):
        add_report(hostname=f"h{i}", findings=FINDINGS[:2])
    page = admin.get("/search", params={"words": "disk"}).text
    link = re.search(r'href="/search\?([^"]+)">Older findings', page)
    assert link is not None
    older = admin.get(f"/search?{html.unescape(link.group(1))}").text
    assert "Older findings" not in older
    assert older.count("<tr>") == 52 - 50 + 1  # two rows and the header
