"""The config generator pages."""

from __future__ import annotations

import html
import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydebuprep import checks
from sqlalchemy.orm import Session

from conftest import csrf_of, make_team
from pydebuprep_portal.models import ConfigVersion

MEMBER = ("pydebuprep-team-web",)
PAGE = "/configs/web/default"


@pytest.fixture
def member(signed_in: Callable[..., TestClient], db: Session) -> TestClient:
    make_team(db, "web")
    client = signed_in(*MEMBER)
    resp = client.post("/configs", data={"csrf_token": csrf_of(client), "team": "web", "name": "default"},
                       follow_redirects=False)  # fmt: skip
    assert (resp.status_code, resp.headers["location"]) == (303, PAGE)
    return client


def the_form(client: TestClient) -> dict[str, list[str] | str]:
    """The generator form as the page renders it: every field with its current value."""
    page = client.get(PAGE).text
    form: dict[str, list[str] | str] = {"csrf_token": csrf_of(client)}
    form["_fields"] = re.findall(r'name="_fields" value="([^"]+)"', page)
    for name, value in re.findall(
        r'<input[^>]*name="([^"_][^"]*)"[^>]*value="([^"]*)"[^>]*>', page
    ):
        if name not in ("csrf_token",):
            form[name] = html.unescape(value)
    for name in re.findall(r'<input type="checkbox"[^>]*name="([^"]+)"[^>]*checked', page):
        form[name] = "1"
    for name in re.findall(
        r'<input type="checkbox"[^>]*name="([^"]+)"(?![^>]*checked)[^>]*>', page
    ):
        form.pop(name, None)
    for name, selected in re.findall(
        r'<select name="([^"]+)"[^>]*>.*?<option value="([^"]*)" selected', page, re.S
    ):
        form[name] = selected
    for name, text in re.findall(r'<textarea name="([^"]+)"[^>]*>([^<]*)</textarea>', page):
        form[name] = html.unescape(text)
    return form


def save(client: TestClient, form: dict[str, list[str] | str]):  # type: ignore[no-untyped-def]
    return client.post(PAGE, data=form, follow_redirects=False)


def test_member_creates_and_edits_through_the_form(member: TestClient, db: Session) -> None:
    form = the_form(member)
    assert form["report.min_severity"] == "info"
    form["report.min_severity"] = "warning"
    form.pop("checks.debsums.enabled")
    resp = save(member, form)
    assert (resp.status_code, resp.headers["location"]) == (303, PAGE)
    versions = db.query(ConfigVersion).order_by(ConfigVersion.version).all()
    assert versions[-1].values == {
        "report": {"min_severity": "warning"},
        "checks": {"debsums": {"enabled": False}},
    }
    page = member.get(PAGE).text
    assert "min_severity: warning" in page
    assert the_form(member)["report.min_severity"] == "warning"
    assert "checks.debsums.enabled" not in the_form(member)


def test_form_offers_every_check_and_no_forbidden_option(member: TestClient) -> None:
    page = member.get(PAGE).text
    for cls in checks.ALL_CHECKS:
        assert f'name="checks.{cls.id}.enabled"' in page, cls.id
    for forbidden in (
        "credentials_file",
        "search_paths",
        "http_probes",
        "output_dir",
        "snapshot_dir",
        "upload.",
    ):
        assert f'.{forbidden}"' not in page and f'name="{forbidden}' not in page, forbidden


def test_bad_input_keeps_what_was_typed(member: TestClient, db: Session) -> None:
    form = the_form(member)
    option = next(
        k for k in form if k.startswith("checks.disk-space.") and k != "checks.disk-space.enabled"
    )
    form[option] = "abc"
    resp = save(member, form)
    assert resp.status_code == 422
    assert 'value="abc"' in resp.text
    assert option.split(".", 2)[2] in resp.text and 'class="notice"' in resp.text
    assert db.query(ConfigVersion).count() == 1


def test_pydebuprep_refusals_are_shown(member: TestClient, db: Session) -> None:
    form = the_form(member)
    form["logs.ignore"] = "("
    resp = save(member, form)
    assert resp.status_code == 422 and "invalid pattern" in resp.text
    assert "(</textarea>" in resp.text


def test_unchanged_save_says_so(member: TestClient, db: Session) -> None:
    resp = save(member, the_form(member))
    assert resp.headers["location"] == PAGE + "?unchanged=1"
    assert "No changes" in member.get(resp.headers["location"]).text
    assert db.query(ConfigVersion).count() == 1


def test_auditor_reads_disabled_form(
    member: TestClient, signed_in: Callable[..., TestClient]
) -> None:
    auditor = signed_in("pydebuprep-auditors")
    page = auditor.get(PAGE).text
    assert "<fieldset disabled" in page and 'type="submit">Save' not in page
    form = the_form(member) | {"csrf_token": csrf_of(auditor)}
    assert auditor.post(PAGE, data=form).status_code == 403
    assert 'action="/configs"' not in auditor.get("/configs").text


def test_other_team_gets_404(member: TestClient, signed_in: Callable[..., TestClient]) -> None:
    other = signed_in("pydebuprep-team-db")
    assert other.get(PAGE).status_code == 404
    assert other.get(PAGE + "/versions/1").status_code == 404
    assert other.post(PAGE, data={"csrf_token": csrf_of(other)}).status_code == 404
    assert "web/default" not in other.get("/configs").text


def test_versions_are_viewable(member: TestClient) -> None:
    form = the_form(member)
    form["report.todo"] = "1"
    save(member, form)
    page = member.get(PAGE).text
    assert (
        'href="/configs/web/default/versions/1"' in page
        and 'href="/configs/web/default/versions/2"' in page
    )
    first = member.get(PAGE + "/versions/1").text
    assert "version 1" in first and "todo: true" not in first
    assert "todo: true" in member.get(PAGE + "/versions/2").text
    assert member.get(PAGE + "/versions/99").status_code == 404


def test_config_forms_need_csrf(member: TestClient) -> None:
    assert member.post("/configs", data={"team": "web", "name": "x"}).status_code == 403
    assert member.post(PAGE, data={"report.todo": "1"}).status_code == 403


def test_page_shows_the_fetch_command(member: TestClient) -> None:
    assert (
        "sudo pydebuprep --config https://portal.test/api/v1/configs/web/default"
        in member.get(PAGE).text
    )


def test_create_errors(member: TestClient, signed_in: Callable[..., TestClient]) -> None:
    resp = member.post(
        "/configs", data={"csrf_token": csrf_of(member), "team": "web", "name": "default"}
    )
    assert resp.status_code == 422 and "already has" in resp.text
    resp = member.post("/configs", data={"csrf_token": csrf_of(member), "team": "db", "name": "x"})
    assert resp.status_code == 404
    assert '<option value="web"' in member.get("/configs").text
    assert 'href="/configs/web/default"' in member.get("/configs").text


def test_admins_create_configs_for_any_team(
    signed_in: Callable[..., TestClient], db: Session
) -> None:
    make_team(db, "web")
    make_team(db, "db")
    page = signed_in("pydebuprep-admins").get("/configs").text
    assert '<option value="web"' in page and '<option value="db"' in page


def test_unknown_fields_in_a_failing_form_are_ignored(member: TestClient) -> None:
    form = the_form(member)
    form["_fields"] = [*form["_fields"], "nope"]  # type: ignore[misc]
    form["logs.ignore"] = "("
    assert save(member, form).status_code == 422
