"""Stored reports in every format pydebuprep renders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pydebuprep.render import render
from pydebuprep.render._common import TodoItem, clean_report, todo_items
from pydebuprep.reportfile import load_report

FILE_EXTENSIONS = {
    "text": "txt",
    "json": "json",
    "yaml": "yaml",
    "html": "html",
    "markdown": "md",
    "jira": "jira.txt",
    "jira-legacy": "jira.txt",
    "jira-cloud": "jira-cloud.md",
    "confluence-legacy": "confluence.txt",
    "confluence-cloud": "confluence-cloud.md",
    "confluence": "confluence.txt",
}
MEDIA_TYPES = {
    "text": "text/plain; charset=utf-8",
    "json": "application/json",
    "yaml": "application/yaml; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "jira": "text/plain; charset=utf-8",
    "jira-legacy": "text/plain; charset=utf-8",
    "jira-cloud": "text/markdown; charset=utf-8",
    "confluence-legacy": "text/plain; charset=utf-8",
    "confluence-cloud": "text/markdown; charset=utf-8",
    "confluence": "text/plain; charset=utf-8",
}


@dataclass(frozen=True)
class DownloadLink:
    fmt: str
    label: str
    copy: str = ""  # what its Copy button says it copies for; empty without a button


@dataclass(frozen=True)
class DownloadGroup:
    name: str
    links: tuple[DownloadLink, ...]


# What the report page links to. "jira" and "confluence" are the same files as the -legacy
# formats, so they aren't linked as well; their addresses keep working. The Jira and
# Confluence formats are made for pasting, so they can be copied without a file.
DOWNLOAD_GROUPS = (
    DownloadGroup(
        "Jira",
        (
            DownloadLink("jira-cloud", "Cloud", "Jira Cloud"),
            DownloadLink("jira-legacy", "On-prem", "on-prem Jira"),
        ),
    ),
    DownloadGroup(
        "Confluence",
        (
            DownloadLink("confluence-cloud", "Cloud", "Confluence Cloud"),
            DownloadLink("confluence-legacy", "On-prem", "on-prem Confluence"),
        ),
    ),
    DownloadGroup(
        "Other",
        tuple(DownloadLink(f, f) for f in ("text", "json", "yaml", "html", "markdown")),
    ),
)
DOWNLOAD_FORMATS = tuple(link.fmt for group in DOWNLOAD_GROUPS for link in group.links)
# A report's HTML is shown in a sandboxed frame: no scripts, no requests, its own
# inline stylesheet only, framed only by the portal itself.
REPORT_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"


def render_stored(body: str, fmt: str, *, todo: bool = False) -> str:
    """A stored report (JSON, validated when it was uploaded) in FMT, one of FORMATS."""
    return render(load_report(body), fmt, todo=todo)


def todo_of(body: str) -> list[TodoItem]:
    """A stored report's TODO list. Uploads don't carry one, but it comes from the findings."""
    return todo_items(clean_report(load_report(body)))


def download_name(host: str, generated_at: datetime, fmt: str) -> str:
    """`<host>-<UTC time>.<ext>`, with the host reduced to [a-z0-9.-]."""
    safe = re.sub(r"\.{2,}", ".", re.sub(r"[^a-z0-9.-]", "", host.lower())).strip(".")
    stamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe or 'report'}-{stamp}.{FILE_EXTENSIONS[fmt]}"
