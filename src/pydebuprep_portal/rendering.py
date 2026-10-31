"""Stored reports in every format pydebuprep renders."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydebuprep.render import render
from pydebuprep.reportfile import load_report

FILE_EXTENSIONS = {
    "text": "txt",
    "json": "json",
    "yaml": "yaml",
    "html": "html",
    "markdown": "md",
    "jira": "jira.txt",
    "confluence": "confluence.txt",
}
MEDIA_TYPES = {
    "text": "text/plain; charset=utf-8",
    "json": "application/json",
    "yaml": "application/yaml; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "jira": "text/plain; charset=utf-8",
    "confluence": "text/plain; charset=utf-8",
}
# A report's HTML is shown in a sandboxed frame: no scripts, no requests, its own
# inline stylesheet only, framed only by the portal itself.
REPORT_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"


def render_stored(body: str, fmt: str) -> str:
    """A stored report (JSON, validated when it was uploaded) in FMT, one of FORMATS."""
    return render(load_report(body), fmt)


def download_name(host: str, generated_at: datetime, fmt: str) -> str:
    """`<host>-<UTC time>.<ext>`, with the host reduced to [a-z0-9.-]."""
    safe = re.sub(r"\.{2,}", ".", re.sub(r"[^a-z0-9.-]", "", host.lower())).strip(".")
    stamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe or 'report'}-{stamp}.{FILE_EXTENSIONS[fmt]}"
