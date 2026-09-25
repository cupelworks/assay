from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assay.config import settings

# Single engine instance shared across the app — one connection pool for the process.
engine = create_async_engine(
    settings.database_url,
    # Echo SQL to stdout when log level is DEBUG — useful locally, too noisy in prod.
    echo=settings.log_level == "DEBUG",
)


# SQLite does not enforce foreign keys by default — this enables ON DELETE CASCADE.
# Guarded to SQLite only: PRAGMA is SQLite-specific syntax and would error
# outright the first time it ran against a real Postgres connection.
if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# expire_on_commit=False keeps ORM objects usable after a commit without re-querying.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session per request, closes it on exit."""
    async with AsyncSessionLocal() as session:
        yield session
