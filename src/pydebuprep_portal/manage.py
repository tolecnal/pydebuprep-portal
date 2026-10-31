"""Command line for running the portal: `python -m pydebuprep_portal.manage`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from alembic import command

from pydebuprep_portal.access import TEAM_NAME, Viewer, valid_team_name
from pydebuprep_portal.configs import ConfigError, create_config, served_url
from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
from pydebuprep_portal.retention import expire_reports
from pydebuprep_portal.settings import SettingsError, load_settings
from pydebuprep_portal.teams import create_team, get_team
from pydebuprep_portal.tokens import create_token, token_request

# The command line acts as an admin: whoever runs it runs the portal.
MANAGE = Viewer("manage", "manage", frozenset({"admin"}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pydebuprep_portal.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema up to date")
    sub.add_parser("expire", help="delete expired reports now (the portal does it daily)")
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    name, expires = "", None
    if args.command == "create-token":
        try:
            name, expires = token_request(args.name, args.expires_days, datetime.now(UTC))
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
    if args.command == "create-config":
        with make_sessionmaker(engine)() as session:
            if get_team(session, args.team) is None:
                create_team(session, "manage", args.team)
            try:
                create_config(session, MANAGE, settings, args.team, args.config, actor="manage")
            except ConfigError as exc:
                print(f"error: {exc.detail}", file=sys.stderr)
                return 1
        engine.dispose()
        print(served_url(settings, args.team, args.config))
        return 0
    if args.command == "expire":
        with make_sessionmaker(engine)() as session:
            deleted = expire_reports(session, datetime.now(UTC))
        engine.dispose()
        print(f"Expired {deleted} reports.")
        return 0
    with make_sessionmaker(engine)() as session:
        team_id = None
        if args.team:
            team = get_team(session, args.team) or create_team(session, "manage", args.team)
            team_id = team.id
        kind = "host" if args.host else "team" if args.team else "global"
        _, plain = create_token(
            session, kind=kind, name=name, actor="manage", expires_at=expires,
            team_id=team_id, host_name=args.host,
        )  # fmt: skip
    engine.dispose()
    print(plain)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
