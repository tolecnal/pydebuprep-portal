"""Configs and their versions: who creates, saves and reads them."""

from __future__ import annotations

import threading

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from conftest import make_team, test_settings
from pydebuprep_portal.access import Viewer
from pydebuprep_portal.configs import (
    ConfigError,
    create_config,
    etag,
    get_config,
    get_version,
    list_configs,
    newest,
    render_served,
    save_config,
    served_url,
)
from pydebuprep_portal.db import make_sessionmaker
from pydebuprep_portal.models import AuditEntry, ConfigVersion

SETTINGS = test_settings()
MEMBER = Viewer("m", "Mia", frozenset(), teams=frozenset({"web"}))
AUDITOR = Viewer("a", "Aud", frozenset({"auditor"}))
OTHER = Viewer("o", "Oz", frozenset(), teams=frozenset({"db"}), leads=frozenset({"db"}))
ADMIN = Viewer("x", "Ada", frozenset({"admin"}))
CHANGED = {"report": {"min_severity": "warning"}}


@pytest.fixture(autouse=True)
def teams(db: Session) -> None:
    make_team(db, "web")
    make_team(db, "db")


def test_member_creates_and_saves(db: Session) -> None:
    config = create_config(db, MEMBER, SETTINGS, "web", "default")
    assert [(v.version, v.values) for v in config.versions] == [(1, {})]
    saved = save_config(db, MEMBER, SETTINGS, "web", "default", CHANGED)
    assert saved is not None and (saved.version, saved.values) == (2, CHANGED)
    assert saved.created_by == "Mia <m>"
    actions = [
        (e.action, e.target, e.details) for e in db.query(AuditEntry).order_by(AuditEntry.id)
    ]
    assert actions == [
        ("config.create", "web/default", {}),
        ("config.save", "web/default", {"version": 2}),
    ]


def test_saving_the_same_values_is_a_no_op(db: Session) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "default")
    save_config(db, MEMBER, SETTINGS, "web", "default", CHANGED)
    assert (
        save_config(
            db,
            MEMBER,
            SETTINGS,
            "web",
            "default",
            {"report": {"min_severity": "warning", "format": "text"}},
        )
        is None
    )
    assert db.query(AuditEntry).filter_by(action="config.save").count() == 1


def test_auditor_reads_but_cannot_edit(db: Session) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "default")
    with pytest.raises(ConfigError) as exc:
        save_config(db, AUDITOR, SETTINGS, "web", "default", CHANGED)
    assert exc.value.status == 403
    with pytest.raises(ConfigError) as exc:
        create_config(db, AUDITOR, SETTINGS, "web", "other")
    assert exc.value.status == 403
    assert get_config(db, AUDITOR, "web", "default") is not None


def test_other_team_cannot_see(db: Session) -> None:
    make_team(db, "web", public=True)
    create_config(db, MEMBER, SETTINGS, "web", "default")
    assert get_config(db, OTHER, "web", "default") is None
    for call in (lambda: save_config(db, OTHER, SETTINGS, "web", "default", CHANGED),
                 lambda: create_config(db, OTHER, SETTINGS, "web", "mine"),
                 lambda: save_config(db, ADMIN, SETTINGS, "web", "nope", CHANGED),
                 lambda: create_config(db, ADMIN, SETTINGS, "nope", "x")):  # fmt: skip
        with pytest.raises(ConfigError) as exc:
            call()
        assert exc.value.status == 404


@pytest.mark.parametrize("name", ["", "Default", "a b", "-x", "a" * 64, "taken"])
def test_bad_names(db: Session, name: str) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "taken")
    with pytest.raises(ConfigError) as exc:
        create_config(db, MEMBER, SETTINGS, "web", name)
    assert exc.value.status == 422


def test_invalid_values_save_nothing(db: Session) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "default")
    with pytest.raises(ConfigError) as exc:
        save_config(db, MEMBER, SETTINGS, "web", "default", {"report": {"output_dir": "/etc"}})
    assert exc.value.status == 422
    assert exc.value.errors == ["report.output_dir: can't be set in a config served by the portal"]
    assert db.query(ConfigVersion).count() == 1
    assert db.query(AuditEntry).filter_by(action="config.save").count() == 0


def test_concurrent_saves_number_versions_in_order(engine: Engine, db: Session) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "default")
    results: list[int] = []

    def save(severity: str) -> None:
        with make_sessionmaker(engine)() as session:
            saved = save_config(
                session, MEMBER, SETTINGS, "web", "default", {"report": {"min_severity": severity}}
            )
            assert saved is not None
            results.append(saved.version)

    threads = [threading.Thread(target=save, args=(s,)) for s in ("warning", "critical")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == [2, 3]
    assert db.query(AuditEntry).filter_by(action="config.save").count() == 2


def test_list_configs_per_viewer(db: Session) -> None:
    create_config(db, MEMBER, SETTINGS, "web", "default")
    save_config(db, MEMBER, SETTINGS, "web", "default", CHANGED)
    create_config(db, OTHER, SETTINGS, "db", "default")
    assert [(c.team, c.name, c.version) for c in list_configs(db, MEMBER)] == [
        ("web", "default", 2)
    ]
    assert [(c.team, c.name) for c in list_configs(db, AUDITOR)] == [
        ("db", "default"),
        ("web", "default"),
    ]
    assert list_configs(db, MEMBER)[0].updated_by == "Mia <m>"


def test_render_served_has_the_portal_upload_url(db: Session) -> None:
    config = create_config(db, MEMBER, SETTINGS, "web", "default")
    save_config(db, MEMBER, SETTINGS, "web", "default", CHANGED)
    db.refresh(config)
    text = render_served(SETTINGS, config, newest(config))
    url = served_url(SETTINGS, "web", "default")
    assert url == "https://portal.test/api/v1/configs/web/default"
    assert text.splitlines()[0] == f"# pydebuprep config web/default, version 2, from {url}"
    assert yaml.safe_load(text) == {
        "report": {"min_severity": "warning"},
        "upload": {"url": "https://portal.test"},
    }
    tag = etag(text)
    assert tag == etag(text) and tag.startswith('"') and tag.endswith('"') and len(tag) == 34
    first = get_version(config, 1)
    assert first is not None and etag(render_served(SETTINGS, config, first)) != tag
    assert get_version(config, 9) is None
