"""Alembic environment: runs the migrations online against sqlalchemy.url."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from pydebuprep_portal import models  # noqa: F401  (registers the tables)
from pydebuprep_portal.db import Base

url = context.config.get_main_option("sqlalchemy.url") or os.environ["PORTAL_DATABASE_URL"]
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
