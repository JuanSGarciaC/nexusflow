"""Entorno de Alembic (modo asíncrono).

La URL de la base de datos se toma de NEXUSFLOW_DATABASE_URL para no versionar
credenciales en alembic.ini. Solo se necesita esa variable para migrar.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Importar los modelos registra sus tablas en Base.metadata (necesario para autogenerate).
import nexusflow.db.models  # noqa: F401
from nexusflow.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("NEXUSFLOW_DATABASE_URL")
    if not url:
        raise RuntimeError("Define NEXUSFLOW_DATABASE_URL para ejecutar migraciones.")
    return url


def run_migrations_offline() -> None:
    """Genera SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
