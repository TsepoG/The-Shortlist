"""Alembic environment.

Reads `DATABASE_URL` from the environment via `shortlist.config`, not from
`alembic.ini`, so CI and local development share exactly one source of truth for
the connection string.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from shortlist.config import database_url
from shortlist.data._backends.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Overrides whatever (nothing) is in alembic.ini's sqlalchemy.url.
config.set_main_option("sqlalchemy.url", database_url())

target_metadata = metadata


def run_migrations_offline() -> None:
    """Generate SQL against a URL without a live DB connection (`--sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection — the normal path."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
