"""Formats, file names, vendored files and the templates' CSP hygiene."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydebuprep.render import FORMATS

from pydebuprep_portal.rendering import (
    DOWNLOAD_FORMATS,
    DOWNLOAD_GROUPS,
    FILE_EXTENSIONS,
    MEDIA_TYPES,
    download_name,
    render_stored,
    todo_of,
)
from sample import sample_json

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "pydebuprep_portal"
WHEN = datetime(2026, 10, 31, 9, 0, 5, tzinfo=UTC)


def test_file_extensions_cover_every_format() -> None:
    assert set(FILE_EXTENSIONS) == set(FORMATS)
    assert set(MEDIA_TYPES) == set(FORMATS)


@pytest.mark.parametrize(
    ("host", "fmt", "name"),
    [
        ("web1", "text", "web1-20261031T090005Z.txt"),
        ("../Web 1", "markdown", "web1-20261031T090005Z.md"),
        ("a..b", "json", "a.b-20261031T090005Z.json"),
        ("../..", "html", "report-20261031T090005Z.html"),
        ('x"; y', "jira", "xy-20261031T090005Z.jira.txt"),
        ("web1", "jira-legacy", "web1-20261031T090005Z.jira.txt"),
        ("web1", "jira-cloud", "web1-20261031T090005Z.jira-cloud.md"),
        ("web1", "confluence-legacy", "web1-20261031T090005Z.confluence.txt"),
        ("web1", "confluence-cloud", "web1-20261031T090005Z.confluence-cloud.md"),
    ],
)
def test_download_name_is_safe(host: str, fmt: str, name: str) -> None:
    assert download_name(host, WHEN, fmt) == name


def test_cloud_format_is_served_as_markdown() -> None:
    assert MEDIA_TYPES["jira-cloud"] == "text/markdown; charset=utf-8"
    assert MEDIA_TYPES["confluence-cloud"] == "text/markdown; charset=utf-8"
    assert MEDIA_TYPES["confluence-legacy"] == MEDIA_TYPES["confluence"]
    assert MEDIA_TYPES["jira-legacy"] == MEDIA_TYPES["jira"] == "text/plain; charset=utf-8"


def test_download_links_leave_out_the_aliases() -> None:
    # "jira" and "confluence" are the same files as the -legacy formats; their addresses
    # keep working.
    assert set(DOWNLOAD_FORMATS) == set(FORMATS) - {"jira", "confluence"}
    assert {"jira-legacy", "jira-cloud", "confluence-legacy", "confluence-cloud"} <= set(
        DOWNLOAD_FORMATS
    )


def test_download_groups_offer_copying_only_what_gets_pasted() -> None:
    copied = {link.fmt for group in DOWNLOAD_GROUPS for link in group.links if link.copy}
    assert copied == {"jira-cloud", "jira-legacy", "confluence-cloud", "confluence-legacy"}
    assert [group.name for group in DOWNLOAD_GROUPS] == ["Jira", "Confluence", "Other"]


def test_todo_of_is_built_from_the_findings() -> None:
    body = sample_json(remediation=("Fix it",))
    items = todo_of(body)
    assert [(i.severity, i.check, i.title, i.steps) for i in items[:2]] == [
        ("critical", "apt-sources", "Repository still targets bookworm", ["Fix it"]),
        ("warning", "disk-space", "Disk is full on /var", ["Fix it"]),
    ]
    assert items[-1].severity is None  # the final confirmation run


def test_todo_of_is_empty_with_nothing_to_do() -> None:
    assert todo_of(sample_json(findings=())) == []


def test_todo_of_removes_control_characters() -> None:
    items = todo_of(sample_json(findings=(("disk-space", "warning", "bad \x1b[2J title"),)))
    assert items[0].title == "bad ?[2J title"


def test_render_stored_adds_the_todo_list_when_asked() -> None:
    body = sample_json()
    assert "TODO" not in render_stored(body, "jira-cloud")
    assert "## TODO" in render_stored(body, "jira-cloud", todo=True)


def test_download_name_in_utc() -> None:
    from datetime import timedelta, timezone

    local = WHEN.astimezone(timezone(timedelta(hours=2)))
    assert download_name("h", local, "yaml") == "h-20261031T090005Z.yaml"


def test_vendored_files_match_their_hashes() -> None:
    table = (PACKAGE / "static" / "VENDORED.md").read_text()
    rows = re.findall(r"^\| `([^`]+)` \| \S+ \| `([0-9a-f]{64})` \|$", table, re.MULTILINE)
    assert {name for name, _ in rows} == {
        "htmx.min.js", "swagger/swagger-ui-bundle.js", "swagger/swagger-ui.css"
    }  # fmt: skip
    for name, digest in rows:
        assert hashlib.sha256((PACKAGE / "static" / name).read_bytes()).hexdigest() == digest, name


def test_templates_have_no_inline_script_or_style() -> None:
    for template in (PACKAGE / "templates").glob("*.html"):
        text = template.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), template.name
        assert "style=" not in text and "<style" not in text, template.name
        assert re.search(r"\bon[a-z]+=", text) is None, template.name


def test_htmx_indicator_styles_are_off() -> None:
    assert "includeIndicatorStyles" in (PACKAGE / "templates" / "base.html").read_text()
