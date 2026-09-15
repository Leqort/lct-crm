"""Append-only audit trail (SPEC §4.5, §5).

The table is protected by a database trigger that rejects UPDATE and DELETE, so
even a bug in application code cannot rewrite history.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import AUDIT_ACTION_ENUM, AuditAction


class AuditLog(Base):
    __tablename__ = "audit_log"
    __audit__ = False  # never audit the audit
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}  # type: ignore[misc]

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(always=False), primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    # Denormalised on purpose: the user row may be removed, the log must remain
    # readable without a join.
    actor_name: Mapped[str | None] = mapped_column(sa.Text, default=None)
    action: Mapped[AuditAction] = mapped_column(
        sa.Enum(
            AuditAction,
            name=AUDIT_ACTION_ENUM,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    # {field: {"old": ..., "new": ...}} — changed fields only; personal data is
    # stored as hashes (see app/core/audit.py).
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    user_agent: Mapped[str | None] = mapped_column(sa.Text, default=None)
    request_id: Mapped[uuid.UUID | None] = mapped_column(default=None)

    __table_args__ = (
        sa.Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),
        sa.Index("ix_audit_log_actor_id_occurred_at", "actor_id", "occurred_at"),
        sa.Index("ix_audit_log_occurred_at", "occurred_at"),
        sa.Index("ix_audit_log_action", "action"),
    )
