from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this so
    Base.metadata holds every table definition (needed by Alembic autogenerate)."""
