"""The Docker image, compose file, settings template and README."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from pydebuprep.reportfile import load_report

from conftest import BASE_ENV
from pydebuprep_portal.app import from_environment

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_base_and_user() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert re.findall(r"^FROM (\S+)", text, re.MULTILINE) == ["debian:trixie-slim"]
    assert "USER portal" in text
    assert "--require-hashes" in text
    assert "--no-build-isolation" in text  # build tools come from the hashed lock too
    assert "HEALTHCHECK" in text
    assert "--no-install-recommends" in text
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
    assert "pydebuprep_portal.manage migrate" in entrypoint
    assert "--factory pydebuprep_portal.app:from_environment" in entrypoint
    assert "--proxy-headers" in entrypoint


def test_compose_runs_portal_and_postgres() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]
    assert set(services) == {"db", "portal"}
    assert services["db"]["image"].startswith("postgres:17")
    assert services["portal"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert services["portal"]["ports"] == ["127.0.0.1:${PORTAL_PORT:-8000}:8000"]
    assert "healthcheck" in services["db"]


def test_env_example_lists_every_setting() -> None:
    read = set(
        re.findall(r'"(PORTAL_[A-Z_]+)"', (ROOT / "src/pydebuprep_portal/settings.py").read_text())
    )
    example = (ROOT / ".env.example").read_text()
    assert read <= set(re.findall(r"^#? ?(PORTAL_[A-Z_]+)=", example, re.MULTILINE))
    assert "PORTAL_FORWARDED_ALLOW_IPS" in example and "POSTGRES_PASSWORD" in example


def test_requirements_pin_pydebuprep_0_6_0() -> None:
    text = (ROOT / "requirements.txt").read_text()
    assert re.search(
        r"^pydebuprep @ https://github\.com/tolecnal/pydebuprep/archive/refs/tags/v0\.6\.0\.tar\.gz \\\n"
        r"\s+--hash=sha256:[0-9a-f]{64}$",
        text,
        re.MULTILINE,
    )
    build = (ROOT / "requirements-build.txt").read_text()
    pinned = re.search(r"^setuptools==(\S+) \\$", build, re.MULTILINE)
    assert pinned and f"setuptools=={pinned.group(1)} \\" in text  # the same version in both


def test_readme_covers_setup() -> None:
    text = (ROOT / "README.md").read_text()
    for topic in ("docker compose", "Keycloak", "pydebuprep-admins", "pydebuprep-auditors",
                  "manage create-token", "/etc/pydebuprep/token", "upload:", "ID token",
                  "/docs", "PORTAL_BASE_URL"):  # fmt: skip
        assert topic in text, topic


def test_sample_report_is_a_report() -> None:
    report = load_report((ROOT / "tests/data/sample-report.json").read_text())
    assert report.host.hostname == "smoke-test"


def test_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    assert isinstance(from_environment(), FastAPI)


def test_smoke_script_uploads_with_the_cli() -> None:
    text = (ROOT / "scripts/smoke.sh").read_text()
    assert "manage create-token" in text and "--team smoke" in text
    assert "create-config smoke default" in text and "configtest" in text
    assert "upload --allow-http" in text
    assert "docker compose" in text and "down -v" in text
    assert "smoke: ok" in text


def test_readme_covers_teams() -> None:
    text = (ROOT / "README.md").read_text()
    for topic in ("pydebuprep-team-", "-lead", "unassigned", "keep forever", "audit log",
                  "only admins map hosts", "host token", "team token"):  # fmt: skip
        assert topic in text, topic
    assert "PORTAL_MAX_UPLOAD_BYTES" not in text


def test_readme_covers_configs() -> None:
    text = (ROOT / "README.md").read_text()
    for topic in ("api/v1/configs", "--config https://", "version", "credentials_file",
                  "manage create-config"):  # fmt: skip
        assert topic in text, topic


def test_the_old_upload_limit_variable_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PORTAL_MAX_UPLOAD_BYTES", "1000")
    with caplog.at_level("WARNING"):
        from_environment()
    assert "PORTAL_MAX_UPLOAD_BYTES is no longer read" in caplog.text


def test_security_workflows() -> None:
    security = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text())
    names = {job["name"] for job in security["jobs"].values()}
    assert {"Bandit (Python security lint)", "ShellCheck", "Known vulnerabilities in Python dependencies",
            "zizmor (GitHub Actions security)", "Dependency review"} <= names  # fmt: skip
    codeql = yaml.safe_load((ROOT / ".github/workflows/codeql.yml").read_text())
    assert codeql["jobs"]["analyze"]["strategy"]["matrix"]["language"] == ["python", "actions"]
    assert "make audit" in (ROOT / ".github/workflows/security.yml").read_text()


def test_preview_compose_and_realm() -> None:
    import json

    dev = yaml.safe_load((ROOT / "compose.preview.yaml").read_text())
    assert set(dev["services"]) >= {"keycloak", "portal"}
    env = dev["services"]["portal"]["environment"]
    assert env["PORTAL_OIDC_METADATA_URL"].startswith("http://keycloak:8080/")
    realm = json.loads((ROOT / "preview/realm.json").read_text())
    groups = {g["name"] for g in realm["groups"]}
    assert {
        "pydebuprep-admins",
        "pydebuprep-auditors",
        "pydebuprep-team-web",
        "pydebuprep-team-web-lead",
    } <= groups
    users = {u["username"] for u in realm["users"]}
    assert {"admin", "lead", "member", "auditor"} <= users
    (client,) = [c for c in realm["clients"] if c["clientId"] == "pydebuprep-portal"]
    mappers = {m["protocolMapper"] for m in client["protocolMappers"]}
    assert {"oidc-group-membership-mapper", "oidc-audience-mapper"} <= mappers
    assert "preview:" in (ROOT / "Makefile").read_text()
    assert "make preview" in (ROOT / "README.md").read_text()
