"""Configs: a team's named pydebuprep configs, every save a new version."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from pydebuprep_portal.access import TEAM_NAME, Viewer, may_edit_configs, may_read_configs
from pydebuprep_portal.audit import actor_of, record
from pydebuprep_portal.generator import ConfigValuesError, render_config, validate_values
from pydebuprep_portal.models import Config, ConfigVersion, Team
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.teams import get_team


class ConfigError(Exception):
    def __init__(self, status: int, detail: str, errors: list[str] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.errors = errors or [detail]


@dataclass(frozen=True)
class ConfigRow:
    team: str
    name: str
    version: int
    updated_at: datetime
    updated_by: str


NOT_FOUND = "no such config, or you can't see it"


def served_url(settings: Settings, team: str, name: str) -> str:
    return f"{settings.base_url}/api/v1/configs/{team}/{name}"


def _editable_team(session: Session, viewer: Viewer, team_name: str) -> Team:
    team = get_team(session, team_name)
    if team is None or not may_read_configs(viewer, team.name):
        raise ConfigError(404, "no such team, or you can't see its configs")
    if not may_edit_configs(viewer, team.name):
        raise ConfigError(403, "your roles don't allow changing this team's configs")
    return team


def _validated(settings: Settings, team: str, name: str, values: object) -> dict[str, Any]:
    try:
        return validate_values(values, served_url(settings, team, name), settings.base_url)
    except ConfigValuesError as exc:
        raise ConfigError(422, "; ".join(exc.errors), exc.errors) from None


def create_config(
    session: Session,
    viewer: Viewer,
    settings: Settings,
    team_name: str,
    name: str,
    values: object = None,
    actor: str | None = None,
) -> Config:
    """A new config of TEAM_NAME; version 1 is VALUES, by default pydebuprep's defaults."""
    team = _editable_team(session, viewer, team_name)
    name = name.strip()
    if not TEAM_NAME.fullmatch(name):
        raise ConfigError(
            422, "a config name is lower-case letters, digits and dashes (at most 63)"
        )
    if session.scalar(select(Config.id).where(Config.team_id == team.id, Config.name == name)):
        raise ConfigError(422, f"team {team.name} already has a config {name}")
    first = {} if values is None else _validated(settings, team.name, name, values)
    actor, now = actor or actor_of(viewer), datetime.now(UTC)
    config = Config(team_id=team.id, name=name, created_at=now, created_by=actor)
    config.versions.append(ConfigVersion(version=1, values=first, created_at=now, created_by=actor))
    session.add(config)
    record(session, actor, "config.create", f"{team.name}/{name}")
    try:
        session.commit()
    except IntegrityError:  # created by someone else a moment ago
        session.rollback()
        raise ConfigError(422, f"team {team.name} already has a config {name}") from None
    return config


def _locked(session: Session, viewer: Viewer, team_name: str, name: str) -> Config:
    """The config, locked for a save, so versions are numbered one after another."""
    config = session.scalar(
        select(Config)
        .join(Config.team)
        .where(Team.name == team_name, Config.name == name)
        .with_for_update(of=Config)
    )
    if config is None or not may_read_configs(viewer, team_name):
        session.rollback()
        raise ConfigError(404, NOT_FOUND)
    if not may_edit_configs(viewer, team_name):
        session.rollback()
        raise ConfigError(403, "your roles don't allow changing this team's configs")
    return config


def save_config(
    session: Session, viewer: Viewer, settings: Settings, team_name: str, name: str, values: object
) -> ConfigVersion | None:
    """Save VALUES as the config's next version; None when they are what it has now."""
    config = _locked(session, viewer, team_name, name)
    try:
        normalised = _validated(settings, team_name, name, values)
    except ConfigError:
        session.rollback()
        raise
    current = newest(config)
    if current.values == normalised:
        session.rollback()
        return None
    actor = actor_of(viewer)
    version = ConfigVersion(
        version=current.version + 1,
        values=normalised,
        created_at=datetime.now(UTC),
        created_by=actor,
    )
    config.versions.append(version)
    record(session, actor, "config.save", f"{team_name}/{name}", version=version.version)
    session.commit()
    return version


def get_config(session: Session, viewer: Viewer, team_name: str, name: str) -> Config | None:
    if not may_read_configs(viewer, team_name):
        return None
    return session.scalar(
        select(Config).join(Config.team).where(Team.name == team_name, Config.name == name)
    )


def list_configs(session: Session, viewer: Viewer) -> list[ConfigRow]:
    """The configs VIEWER may read, by team and name, with their newest version."""
    latest = (
        select(ConfigVersion.config_id, func.max(ConfigVersion.version).label("version"))
        .group_by(ConfigVersion.config_id)
        .subquery()
    )
    version = aliased(ConfigVersion)
    query = (
        select(Team.name, Config.name, version)
        .join(Config.team)
        .join(latest, latest.c.config_id == Config.id)
        .join(version, (version.config_id == Config.id) & (version.version == latest.c.version))
        .order_by(Team.name, Config.name)
    )
    if not viewer.reads_all:
        query = query.where(Team.name.in_(sorted(viewer.teams)))
    return [
        ConfigRow(team, name, v.version, v.created_at, v.created_by)
        for team, name, v in session.execute(query)
    ]


def newest(config: Config) -> ConfigVersion:
    return config.versions[-1]


def get_version(config: Config, number: int) -> ConfigVersion | None:
    return next((v for v in config.versions if v.version == number), None)


def render_served(settings: Settings, config: Config, version: ConfigVersion) -> str:
    """The YAML hosts get for VERSION, with this portal's upload URL."""
    team = config.team.name
    url = served_url(settings, team, config.name)
    header = f"pydebuprep config {team}/{config.name}, version {version.version}, from {url}"
    return render_config(version.values, upload_url=settings.base_url, header=header)


def etag(text: str) -> str:
    return '"' + hashlib.sha256(text.encode()).hexdigest()[:32] + '"'


def served_problems(settings: Settings, config: Config, version: ConfigVersion) -> list[str]:
    """Why VERSION is no longer a config hosts would accept (pydebuprep's rules may have
    tightened since it was saved); empty when it still is."""
    url = served_url(settings, config.team.name, config.name)
    try:
        validate_values(version.values, url, settings.base_url)
    except ConfigValuesError as exc:
        return exc.errors
    return []
