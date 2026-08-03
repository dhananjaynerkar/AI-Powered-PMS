"""Safe SQLAlchemy engine construction for PostgreSQL 17."""

from __future__ import annotations

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.engine import make_url

from pms_common.settings import Settings


class DatabaseConfigurationError(RuntimeError):
    """Raised when database configuration is absent or still placeholder-only."""


def database_is_configured(settings: Settings) -> bool:
    """Return whether credentials are sufficient for an intentional connection."""

    if settings.database_url is not None:
        value = settings.database_url.get_secret_value().strip()
        return bool(value) and "CHANGE_ME" not in value
    return (
        settings.postgres_password is not None
        and bool(settings.postgres_password.get_secret_value().strip())
    )


def build_database_url(settings: Settings) -> URL:
    """Build a Psycopg SQLAlchemy URL without rendering secrets."""

    if settings.database_url is not None:
        raw_url = settings.database_url.get_secret_value().strip()
        if not raw_url or "CHANGE_ME" in raw_url:
            raise DatabaseConfigurationError("DATABASE_URL is empty or still uses CHANGE_ME")
        url = make_url(raw_url)
        if url.get_backend_name() != "postgresql":
            raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL")
        if url.get_driver_name() != "psycopg":
            raise DatabaseConfigurationError("DATABASE_URL must use the psycopg driver")
        return url

    if settings.postgres_password is None:
        raise DatabaseConfigurationError("database credentials are not configured")
    password = settings.postgres_password.get_secret_value()
    if not password:
        raise DatabaseConfigurationError("POSTGRES_PASSWORD is empty")
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    )


def create_database_engine(settings: Settings, *, read_only: bool) -> Engine:
    """Create a bounded SQLAlchemy engine; source access defaults to read-only."""

    statement_timeout_ms = settings.db_command_timeout_seconds * 1000
    options = f"-c statement_timeout={statement_timeout_ms}"
    if read_only:
        options += " -c default_transaction_read_only=on"
    connect_args = {
        "connect_timeout": settings.db_connect_timeout_seconds,
        "sslmode": settings.db_ssl_mode,
        "options": options,
    }
    return create_engine(
        build_database_url(settings),
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        echo=settings.db_echo,
    )

