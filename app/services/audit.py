"""Audit service: explicit events and read access to the trail (SPEC §5, §7)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import build_event
from app.models.audit_log import AuditLog
from app.models.enums import AuditAction


async def log_event(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
) -> None:
    """Record an event that no ORM change would produce by itself."""
    session.add(build_event(action, entity_type, entity_id, changes))
    await session.flush()


async def log_access_denied(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    **context: Any,
) -> None:
    """SPEC §8: a refusal is itself an auditable event.

    Written in its own transaction: the caller is about to raise, and the
    request transaction will be rolled back — the refusal must survive that.
    """
    session.add(build_event(AuditAction.ACCESS_DENIED, entity_type, entity_id, context or None))
    await session.commit()


async def log_read_pd(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    **context: Any,
) -> None:
    """Reading personal data is logged separately (SPEC §5.4)."""
    session.add(build_event(AuditAction.READ_PD, entity_type, entity_id, context or None))
    await session.flush()


async def log_export(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    **context: Any,
) -> None:
    session.add(build_event(AuditAction.EXPORT, entity_type, entity_id, context or None))
    await session.flush()


async def search_audit_log(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    action: AuditAction | None = None,
    date_from: dt.datetime | None = None,
    date_to: dt.datetime | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[AuditLog], int]:
    """Admin-only, read-only view over the trail (SPEC §5.6)."""
    conditions = []
    if actor_id is not None:
        conditions.append(AuditLog.actor_id == actor_id)
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        conditions.append(AuditLog.entity_id == entity_id)
    if action is not None:
        conditions.append(AuditLog.action == action)
    if date_from is not None:
        conditions.append(AuditLog.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.occurred_at <= date_to)

    where = sa.and_(*conditions) if conditions else sa.true()

    total = await session.scalar(sa.select(sa.func.count()).select_from(AuditLog).where(where))
    stmt = (
        sa.select(AuditLog)
        .where(where)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = list((await session.scalars(stmt)).all())
    return rows, int(total or 0)
