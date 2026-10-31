"""Command line for running the portal: `python -m pydebuprep_portal.manage`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

from alembic import command

from pydebuprep_portal.db import make_engine, make_sessionmaker, migrations_config
from pydebuprep_portal.settings import SettingsError, load_settings
from pydebuprep_portal.tokens import create_token

MAX_EXPIRES_DAYS = 3650


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
    if args.command == "create-token":
        if not args.name.strip():
            parser.error("the token needs a name")
        if args.expires_days is not None and not 0 < args.expires_days <= MAX_EXPIRES_DAYS:
            parser.error(f"--expires-days must be between 1 and {MAX_EXPIRES_DAYS}")
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
    expires = None
    if args.expires_days is not None:
        expires = datetime.now(UTC) + timedelta(days=args.expires_days)
    with make_sessionmaker(engine)() as session:
        _, plain = create_token(
            session, kind="global", name=args.name.strip(), created_by="manage", expires_at=expires
        )
    engine.dispose()
    print(plain)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
