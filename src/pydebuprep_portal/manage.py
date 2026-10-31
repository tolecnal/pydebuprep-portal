"""Command line for running the portal: `python -m pydebuprep_portal.manage`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from alembic import command
from sqlalchemy.orm import Session

from pydebuprep_portal.access import TEAM_NAME, Viewer, valid_team_name
from pydebuprep_portal.configs import ConfigError, create_config, served_url
from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
from pydebuprep_portal.demo import DemoError, load_demo_data
from pydebuprep_portal.retention import expire_reports
from pydebuprep_portal.settings import Settings, SettingsError, load_settings
from pydebuprep_portal.teams import create_team, get_team
from pydebuprep_portal.tokens import create_token, token_request

# The command line acts as an admin: whoever runs it runs the portal.
MANAGE = Viewer("manage", "manage", frozenset({"admin"}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pydebuprep_portal.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema up to date")
    sub.add_parser("expire", help="delete expired reports now (the portal does it daily)")
    sub.add_parser("demo-data", help="fill an empty portal with demo teams and reports")
    config = sub.add_parser(
        "create-config", help="create a team's config (defaults) and print its URL"
    )
    config.add_argument("team", help="the team (created if missing)")
    config.add_argument("config", help="the config's name, e.g. default")
    token = sub.add_parser("create-token", help="create an upload token and print it")
    token.add_argument("name", help="what the token is for")
    token.add_argument("--expires-days", type=int, help="days until it expires (default: never)")
    token.add_argument("--team", help="make a team token of TEAM (created if missing)")
    token.add_argument("--host", help="with --team: a host token for this one host")
    return parser


def _check_arguments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Usage errors, before anything touches the database."""
    if args.command == "create-token":
        try:
            args.name, args.expires = token_request(args.name, args.expires_days, datetime.now(UTC))
        except ValueError as exc:
            parser.error(str(exc))
        if args.host and not args.team:
            parser.error("--host needs --team: a host token belongs to a team")
        if args.team and not valid_team_name(args.team):
            parser.error(f"not a team name: {args.team!r}")
    if args.command == "create-config":
        if not valid_team_name(args.team):
            parser.error(f"not a team name: {args.team!r}")
        if not TEAM_NAME.fullmatch(args.config):
            parser.error(f"not a config name: {args.config!r}")


def _demo_data(session: Session, settings: Settings, args: argparse.Namespace) -> int:
    try:
        print(load_demo_data(session, settings))
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _create_config(session: Session, settings: Settings, args: argparse.Namespace) -> int:
    if get_team(session, args.team) is None:
        create_team(session, "manage", args.team)
    try:
        create_config(session, MANAGE, settings, args.team, args.config, actor="manage")
    except ConfigError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 1
    print(served_url(settings, args.team, args.config))
    return 0


def _expire(session: Session, settings: Settings, args: argparse.Namespace) -> int:
    print(f"Expired {expire_reports(session, datetime.now(UTC))} reports.")
    return 0


def _create_token(session: Session, settings: Settings, args: argparse.Namespace) -> int:
    team_id = None
    if args.team:
        team = get_team(session, args.team) or create_team(session, "manage", args.team)
        team_id = team.id
    kind = "host" if args.host else "team" if args.team else "global"
    _, plain = create_token(
        session, kind=kind, name=args.name, actor="manage", expires_at=args.expires,
        team_id=team_id, host_name=args.host,
    )  # fmt: skip
    print(plain, file=sys.stderr)
    return 0


COMMANDS = {
    "demo-data": _demo_data,
    "create-config": _create_config,
    "expire": _expire,
    "create-token": _create_token,
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _check_arguments(parser, args)
    try:
        settings = load_settings(os.environ)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "migrate":
        command.upgrade(migrations_config(settings.database_url), "head")
        print("Database schema is up to date.")
        return 0
    engine = make_engine(settings.database_url)
    try:
        with make_sessionmaker(engine)() as session:
            return COMMANDS[args.command](session, settings, args)
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
