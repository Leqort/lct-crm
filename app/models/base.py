"""Declarative base and the mixins every domain table shares (SPEC §4.1)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention: Alembic autogenerate produces stable, diffable
# constraint names instead of PostgreSQL's implicit ones.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: PGUUID(as_uuid=True),
        dt.datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }

    # --- Audit hooks -------------------------------------------------------
    # `__audit__` opts a table into automatic audit logging (SPEC §5.1).
    # `__pd_fields__` lists personal-data columns whose values must never be
    # written to the audit log in the clear (SPEC §5.3).
    __audit__: ClassVar[bool] = False
    __pd_fields__: ClassVar[frozenset[str]] = frozenset()

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(default=None)


class SoftDeleteMixin:
    """Hard DELETE is forbidden project-wide (audit trail + 152-ФЗ)."""

    deleted_at: Mapped[dt.datetime | None] = mapped_column(default=None, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class DomainBase(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Every business table inherits this: UUID PK, timestamps, soft delete."""

    __abstract__ = True
    __audit__ = True

    # Fetch server-generated values (created_at/updated_at) with RETURNING as
    # part of the INSERT/UPDATE. Without it `updated_at` is expired after every
    # flush and reading it would emit a lazy SELECT — illegal outside an await.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}  # type: ignore[misc]
