"""Database engine, sessions and migrations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from fastapi import Path as PathParam
from fastapi import Query, Request
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

MIGRATIONS = Path(__file__).parent / "migrations"

# Ids in URLs fit PostgreSQL's bigint; anything larger is a 422, never a database error.
MAX_ID = 2**63 - 1
RowId = Annotated[int, PathParam(ge=1, le=MAX_ID)]
Before = Annotated[int | None, Query(ge=1, le=MAX_ID)]

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def migrations_config(url: str) -> Config:
    """Alembic's config for the migrations shipped in the package."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def get_session(request: Request) -> Iterator[Session]:
    """A session per request (FastAPI dependency)."""
    maker: sessionmaker[Session] = request.app.state.sessionmaker
    with maker() as session:
        yield session
