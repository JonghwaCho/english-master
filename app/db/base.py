"""SQLAlchemy base declarative class and Flask-SQLAlchemy instance.

Uses SQLAlchemy 2.0 style with Mapped[] type annotations.
"""
from __future__ import annotations

from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


# Flask-SQLAlchemy integration (initialized in app factory)
db = SQLAlchemy(model_class=Base)


class TimestampMixin:
    """Mixin providing created_at / updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
