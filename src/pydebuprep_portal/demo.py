"""Demo data for a local preview: teams, hosts with a few weeks of reports, a config."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydebuprep.model import (
    CheckResult,
    Finding,
    Host,
    Report,
    Severity,
    Status,
    compute_summary,
)
from pydebuprep.render import render
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.configs import create_config
from pydebuprep_portal.ingest import store_report
from pydebuprep_portal.models import Report as StoredReport
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.teams import create_team, get_team, set_visibility
from pydebuprep_portal.tokens import create_token

ADMIN = Viewer("demo", "demo", frozenset({"admin"}))
FINDINGS = {
    "ok": [],
    "info": [("apt-autoremove", "info", "3 packages can be autoremoved")],
    "warning": [
        ("disk-space", "warning", "/var is 87 % full"),
        ("journal-errors", "warning", "12 errors from systemd-resolved in this boot"),
    ],
    "critical": [
        ("apt-sources", "critical", "Repository still targets bookworm"),
        ("systemd-failed", "critical", "nginx.service failed"),
        ("disk-space", "warning", "/var is 91 % full"),
    ],
}
# Each host's worst severity, week by week (oldest first).
STORIES = {
    "web1": ("web", ["ok", "info", "info", "warning", "warning", "critical"]),
    "web2": ("web", ["warning", "warning", "info", "ok", "ok", "ok"]),
    "db1": ("db", ["info", "info", "warning", "info", "info", "info"]),
    "docs1": ("docs", ["ok", "ok", "ok", "ok", "info", "ok"]),
    "new-host": (None, ["warning", "critical"]),
}


class DemoError(Exception):
    pass


def _report(hostname: str, when: datetime, worst: str, week: int) -> str:
    by_check: dict[str, list[Finding]] = {}
    for check, severity, title in FINDINGS[worst]:
        by_check.setdefault(check, []).append(Finding(Severity(severity), title))
    results = [
        CheckResult(check, check, "system", Status.FINDINGS, findings=items)
        for check, items in by_check.items()
    ]
    results.append(CheckResult("fstab", "fstab", "storage", Status.OK))
    kernel = "6.1.0-28-amd64" if week < 3 else "6.12.12-amd64"
    release = ("12", "bookworm") if week < 3 else ("13", "trixie")
    report = Report(
        tool={"name": "pydebuprep", "version": "0.6.0"},
        generated_at=when.isoformat(),
        host=Host(hostname, "debian", release[0], release[1], kernel, True),
        baseline=None,
        summary=compute_summary(results),
        results=results,
    )
    return render(report, "json")


def load_demo_data(session: Session, settings: Settings, now: datetime | None = None) -> str:
    """Fill an empty portal; a summary of what was made. DemoError if it has reports."""
    if session.scalar(select(StoredReport.id).limit(1)) is not None:
        raise DemoError("the portal already has reports; demo data goes into an empty one")
    now = now or datetime.now(UTC)
    tokens = {}
    for name in ("web", "db", "docs"):
        team = get_team(session, name) or create_team(session, "demo", name)
        if name == "docs":
            set_visibility(session, "demo", team, True)
        tokens[name] = create_token(
            session, kind="team", name=f"{name} hosts", actor="demo", expires_at=None,
            team_id=team.id,
        )[0].id  # fmt: skip
    fleet, fleet_plain = create_token(
        session, kind="global", name="demo fleet", actor="demo", expires_at=None
    )
    stored = 0
    for hostname, (team_name, story) in STORIES.items():
        for week, worst in enumerate(story):
            when = now - timedelta(weeks=len(story) - week, hours=len(hostname))
            token_id = tokens[team_name] if team_name else fleet.id
            body = _report(hostname, when, worst, week + 6 - len(story)).encode()
            store_report(session, body, token_id, when + timedelta(minutes=5))
            stored += 1
    create_config(
        session, ADMIN, settings, "web", "default",
        {"report": {"min_severity": "warning", "todo": True}}, actor="demo",
    )  # fmt: skip
    return (
        f"Demo data: 3 teams, {len(STORIES)} hosts, {stored} reports, 1 config.\n"
        f"A global upload token: {fleet_plain}"
    )
