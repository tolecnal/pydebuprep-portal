"""Formats, file names, vendored files and the templates' CSP hygiene."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydebuprep.render import FORMATS

from pydebuprep_portal.rendering import FILE_EXTENSIONS, MEDIA_TYPES, download_name

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
    ],
)
def test_download_name_is_safe(host: str, fmt: str, name: str) -> None:
    assert download_name(host, WHEN, fmt) == name


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
