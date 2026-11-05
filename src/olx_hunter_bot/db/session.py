from typing import Any

from sqlalchemy import Connection, event, inspect, text
from sqlalchemy.schema import CreateColumn
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from olx_hunter_bot.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Applied to every new SQLite connection.

    WAL lets readers and a writer work at the same time, so a background hunt
    saving listings doesn't block the chat agent reading hunts (and vice versa).
    busy_timeout makes a write that does collide wait up to 5s instead of
    failing with "database is locked". synchronous=NORMAL is the recommended
    pairing with WAL: still crash-safe, far fewer fsyncs.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def init_db(database_url: str) -> None:
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url)
    if _engine.dialect.name == "sqlite":
        event.listen(_engine.sync_engine, "connect", _sqlite_pragmas)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn: Connection) -> None:
    """create_all only creates missing tables; add columns that newer models
    introduced to tables created by an older version (e.g. hunts.scheduled).
    New columns need a server_default or to be nullable. A stopgap until the
    schema changes often enough to warrant Alembic."""
    inspector = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                ddl = CreateColumn(column).compile(dialect=sync_conn.dialect)
                sync_conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()


def session() -> AsyncSession:
    if _sessionmaker is None:
        raise RuntimeError("init_db() was not called")
    return _sessionmaker()
