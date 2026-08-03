"""Alembic environment with protected-schema validation."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

import sqlalchemy as sa
from alembic import context
from pms_common.database import build_database_url, create_database_engine
from pms_common.migration_safety import validate_revision_directory
from pms_common.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None
settings = Settings()
versions_path = Path(__file__).resolve().parent / "versions"
validate_revision_directory(versions_path)


def run_migrations_offline() -> None:
    """Configure an offline migration without printing credentials."""

    context.configure(
        url=build_database_url(settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=settings.app_schema,
    )
    context.execute(sa.schema.CreateSchema(settings.app_schema, if_not_exists=True))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run approved revisions with the version table outside ``public``."""

    engine = create_database_engine(settings, read_only=False)
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.schema.CreateSchema(settings.app_schema, if_not_exists=True)
            )
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_schemas=True,
                transaction_per_migration=True,
                version_table_schema=settings.app_schema,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
