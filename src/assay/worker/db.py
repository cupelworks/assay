"""Sync SQLAlchemy engine/session for the Celery worker — separate from
assay/db.py's async one. Per docs/run_execution/dev_notes.md note 4: a
Celery task is a plain synchronous callable, not running inside an
asyncio event loop, and the default (prefork) pool runs each task in a
forked child process — sharing an engine's connection pool across that
fork is a documented SQLAlchemy/Celery hazard, not just an async/sync
style preference. See note 4 for the full reasoning.

Derives a sync-compatible URL from the same ASSAY_DATABASE_URL setting
the API uses, rather than introducing a second "where's the database" env
var: sqlite+aiosqlite:// becomes sqlite:// (stdlib sqlite3, nothing extra
to install), postgresql+asyncpg:// becomes postgresql+psycopg2:// (needs
psycopg2-binary, part of the worker extra).
"""
from collections.abc import Iterator
from contextlib import contextmanager

from celery.signals import worker_process_init
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from assay.config import settings

_ASYNC_TO_SYNC_DRIVER = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql+psycopg2",
}


def _sync_url() -> URL:
    url = make_url(settings.database_url)
    sync_drivername = _ASYNC_TO_SYNC_DRIVER.get(url.drivername, url.drivername)
    return url.set(drivername=sync_drivername)


# Small pool per process: the default prefork pool spawns one child process
# per worker slot (10 by default — see the concurrency line in any worker
# startup log), each with its own pool. A careless per-process size
# multiplies out fast against the database's real connection limit.
engine = create_engine(
    _sync_url(),
    pool_size=1,
    max_overflow=2,
    # Echo SQL to stdout when log level is DEBUG — same convention as assay/db.py.
    echo=settings.log_level == "DEBUG",
)

if engine.dialect.name == "sqlite":
    # SQLite does not enforce foreign keys by default — mirrors assay/db.py's
    # own pragma for the async engine, but guarded: PRAGMA is SQLite-only
    # syntax and would error outright against a real Postgres connection,
    # which assay/db.py's equivalent listener does not currently guard against.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@worker_process_init.connect
def _reset_engine_after_fork(**_kwargs):
    """The prefork pool forks worker child processes after this module (and
    its module-level `engine`) has already been imported in the parent —
    inheriting the parent's pooled connections across that fork corrupts
    them. Disposing here drops whatever was inherited, so each child
    process opens its own fresh connections the first time it needs one.
    """
    engine.dispose()


# expire_on_commit=False keeps ORM objects usable after a commit without
# re-querying, same convention as assay/db.py's async session.
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """One session per task — opened at the start, closed at the end, same
    "one unit of work, one session" shape assay/db.py's get_session gives
    each API request, just per-task instead of per-request.

    Usage inside a task body:
        with get_session() as session:
            ...
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
