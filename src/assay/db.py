from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assay.config import settings

# Single engine instance shared across the app — one connection pool for the process.
engine = create_async_engine(
    settings.database_url,
    # Echo SQL to stdout when log level is DEBUG — useful locally, too noisy in prod.
    echo=settings.log_level == "DEBUG",
)

# expire_on_commit=False keeps ORM objects usable after a commit without re-querying.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session per request, closes it on exit."""
    async with AsyncSessionLocal() as session:
        yield session
