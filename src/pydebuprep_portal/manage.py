"""Command line for running the portal: `python -m pydebuprep_portal.manage`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from alembic import command

from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
from pydebuprep_portal.settings import SettingsError, load_settings
from pydebuprep_portal.tokens import create_token, token_request


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pydebuprep_portal.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema up to date")
    token = sub.add_parser("create-token", help="create a global upload token and print it")
    token.add_argument("name", help="what the token is for")
    token.add_argument("--expires-days", type=int, help="days until it expires (default: never)")
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
    with make_sessionmaker(engine)() as session:
        _, plain = create_token(
            session, kind="global", name=name, created_by="manage", expires_at=expires
        )
    engine.dispose()
    print(plain)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
