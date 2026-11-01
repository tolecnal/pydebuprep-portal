"""Finding hosts by release, kernel and silence; findings by their details and dates;
and everything as CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.export import csv_cell
from pydebuprep_portal.models import Report
from pydebuprep_portal.queries import list_hosts, release_counts, search_findings

AddReport = Callable[..., Report]
Bearer = Callable[..., dict[str, str]]
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def day(n: int) -> str:
    return f"2026-10-{n:02d}T09:00:00+00:00"


@pytest.fixture
def fleet(add_report: AddReport) -> None:
    add_report(
        hostname="old1",
        generated_at=day(1),
        os_version="12",
        codename="bookworm",
        kernel="6.1.0-28-amd64",
    )
    add_report(
        hostname="new1",
        generated_at=day(1),
        os_version="12",
        codename="bookworm",
        kernel="6.1.0-28-amd64",
    )
    add_report(hostname="new1", generated_at=day(30))  # upgraded since
    add_report(hostname="new2", generated_at=day(29), kernel="6.12.9-amd64")  # fmt: skip


# --- hosts ---------------------------------------------------------------------------


def test_hosts_by_the_release_of_their_newest_report(db: Session, fleet: None) -> None:
    assert [h.name for h in list_hosts(db, ADMIN, release="bookworm")] == ["old1"]
    assert [h.name for h in list_hosts(db, ADMIN, release="trixie")] == ["new1", "new2"]
    rows = list_hosts(db, ADMIN)
    assert {h.name: (h.release, h.kernel) for h in rows}["old1"] == (
        "debian 12 (bookworm)",
        "6.1.0-28-amd64",
    )
    assert release_counts(rows) == [("debian 13 (trixie)", 2), ("debian 12 (bookworm)", 1)]


def test_hosts_by_kernel(db: Session, fleet: None) -> None:
    assert [h.name for h in list_hosts(db, ADMIN, kernel="6.12.9")] == ["new2"]


def test_hosts_without_a_report_for_days(db: Session, fleet: None) -> None:
    stale = list_hosts(db, ADMIN, silent_days=7, now=NOW)
    assert [h.name for h in stale] == ["old1"]
    assert list_hosts(db, ADMIN, silent_days=3, now=NOW) == stale
    assert [h.name for h in list_hosts(db, ADMIN, silent_days=0, now=NOW)] == [
        "new1", "new2", "old1",
    ]  # fmt: skip


def test_hosts_page_filters_and_counts(admin: TestClient, fleet: None) -> None:
    page = admin.get("/hosts", params={"release": "bookworm"}).text
    assert 'href="/hosts/old1"' in page and 'href="/hosts/new2"' not in page
    assert '<option value="bookworm" selected>' in page
    counts = admin.get("/hosts").text
    assert "debian 13 (trixie)" in counts and "<strong>2</strong>" in counts
    silent = admin.get("/hosts", params={"silent": "7"}).text
    assert 'href="/hosts/old1"' in silent and 'href="/hosts/new1"' not in silent
    assert 'href="/hosts/new2"' in admin.get("/hosts", params={"kernel": "6.12.9"}).text


@pytest.mark.parametrize("days", ["soon", "-1", "99999999999"])
def test_hosts_page_refuses_a_bad_day_count(admin: TestClient, days: str) -> None:
    page = admin.get("/hosts", params={"silent": days}).text
    assert "Not reported in: a number of days" in page
    assert (
        admin.get("/hosts", params={"silent": days, "format": "csv"})
        .headers["content-type"]
        .startswith("text/html")
    )


def test_hosts_api_filters(admin: TestClient, bearer: Bearer, fleet: None) -> None:
    data = admin.get(
        "/api/v1/hosts", params={"release": "bookworm"}, headers=bearer("pydebuprep-admins")
    ).json()
    assert [h["name"] for h in data["hosts"]] == ["old1"]
    assert data["hosts"][0]["release"] == "debian 12 (bookworm)"
    assert data["hosts"][0]["machines"] == 0


# --- findings ------------------------------------------------------------------------

DETAILED = (
    ("nginx", "warning", "nginx passes requests to sockets that don't exist",
     "Requests sent there fail.", "/run/php/php7.4-fpm.sock (fastcgi_pass in default)"),
    ("disk-space", "warning", "Disk is full on /var"),
)  # fmt: skip


def test_words_match_details_and_evidence(db: Session, add_report: AddReport) -> None:
    add_report(findings=DETAILED)
    hits = search_findings(db, ADMIN, words="php7.4-fpm")
    assert [h.check_id for h in hits] == ["nginx"]
    assert [h.check_id for h in search_findings(db, ADMIN, words="requests fail")] == ["nginx"]
    assert search_findings(db, ADMIN, words="php7.4 missingword") == []


def test_findings_between_dates(db: Session, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=DETAILED[1:])
    add_report(hostname="b", generated_at=day(15), findings=DETAILED[1:])
    add_report(hostname="c", generated_at=day(30), findings=DETAILED[1:])
    hosts = lambda **kw: sorted(h.host for h in search_findings(db, ADMIN, **kw))  # noqa: E731
    assert hosts(since=date(2026, 10, 15)) == ["b", "c"]
    assert hosts(until=date(2026, 10, 15)) == ["a", "b"]  # the whole day
    assert hosts(since=date(2026, 10, 2), until=date(2026, 10, 29)) == ["b"]


def test_search_page_dates(admin: TestClient, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=DETAILED[1:])
    add_report(hostname="b", generated_at=day(15), findings=DETAILED[1:])
    page = admin.get("/search", params={"from": "2026-10-10"}).text
    assert 'href="/hosts/b"' in page and 'href="/hosts/a"' not in page
    assert 'value="2026-10-10"' in page
    bad = admin.get("/search", params={"to": "31/10"}).text
    assert "To: a date such as 2026-10-31" in bad


def test_search_api_dates(admin: TestClient, bearer: Bearer, add_report: AddReport) -> None:
    add_report(hostname="a", generated_at=day(1), findings=DETAILED[1:])
    add_report(hostname="b", generated_at=day(15), findings=DETAILED[1:])
    data = admin.get(
        "/api/v1/search/findings",
        params={"until": "2026-10-10"},
        headers=bearer("pydebuprep-admins"),
    ).json()
    assert [f["host"] for f in data["findings"]] == ["a"]


# --- CSV -----------------------------------------------------------------------------


def rows_of(response: object) -> list[list[str]]:
    assert response.headers["content-type"].startswith("text/csv")  # type: ignore[attr-defined]
    assert "attachment" in response.headers["content-disposition"]  # type: ignore[attr-defined]
    return list(csv.reader(io.StringIO(response.text)))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("value", "cell"),
    [("=1+1", "'=1+1"), ("+x", "'+x"), ("-x", "'-x"), ("@x", "'@x"), ("\tx", "'\tx"),
     ("web1", "web1"), (3, "3"), (None, "")],
)  # fmt: skip
def test_cells_never_start_a_formula(value: object, cell: str) -> None:
    assert csv_cell(value) == cell


def test_hosts_as_csv(admin: TestClient, fleet: None) -> None:
    rows = rows_of(admin.get("/hosts", params={"release": "bookworm", "format": "csv"}))
    assert rows[0][:4] == ["host", "release", "kernel", "newest report"]
    assert [r[0] for r in rows[1:]] == ["old1"]
    assert (
        'href="/hosts?release=bookworm&amp;format=csv"'
        in admin.get("/hosts", params={"release": "bookworm"}).text
    )


def test_findings_as_csv_with_every_match(admin: TestClient, add_report: AddReport) -> None:
    for i in range(3):
        add_report(hostname=f"h{i}", findings=(("x", "warning", "=HYPERLINK(evil)"),))
    rows = rows_of(admin.get("/search", params={"words": "hyperlink", "format": "csv"}))
    assert rows[0] == ["report", "host", "generated", "check", "severity", "finding"]
    assert [r[1] for r in rows[1:]] == ["h2", "h1", "h0"]
    assert rows[1][5] == "'=HYPERLINK(evil)"


def test_reports_as_csv(admin: TestClient, fleet: None) -> None:
    rows = rows_of(admin.get("/", params={"format": "csv"}))
    assert rows[0][:3] == ["report", "host", "team"]
    assert len(rows) == 5
