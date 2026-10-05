# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this so
    Base.metadata holds every table definition (needed by Alembic autogenerate)."""
