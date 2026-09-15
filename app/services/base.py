"""Helpers shared by every service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DuplicateEntityError, ValidationError


@asynccontextmanager
async def integrity_guard(
    session: AsyncSession,
    *,
    duplicate_message: str,
    details: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Translate PostgreSQL constraint violations into domain errors.

    Application-level checks are inherently racy: two concurrent requests can
    both find "no such university" and both insert. The partial unique indexes
    are the real guarantee, and this turns their violation into the same
    user-facing error the pre-check would have produced.
    """
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        payload = dict(details or {})
        if constraint:
            payload["constraint"] = constraint
        if constraint and ("check" in constraint or constraint.startswith("ck_")):
            raise ValidationError(
                "Данные нарушают ограничение целостности", details=payload
            ) from None
        raise DuplicateEntityError(duplicate_message, details=payload) from None


async def count_live(session: AsyncSession, model: Any, *conditions: Any) -> int:
    """Count rows of `model` that match `conditions` and are not soft-deleted."""
    total = await session.scalar(
        sa.select(sa.func.count()).select_from(model).where(model.deleted_at.is_(None), *conditions)
    )
    return int(total or 0)


async def assert_not_referenced(
    session: AsyncSession, entity_name: str, blockers: dict[str, int]
) -> None:
    """Refuse a soft delete while live rows still point at the object.

    Soft-deleting a referenced catalog entry would not remove it from sight: it
    would keep surfacing as a nested object inside every card that references
    it, so the "deleted" row would still be visible to users. Blocking mirrors
    the `ON DELETE RESTRICT` already declared on the foreign keys, loses no data
    and is reversible — unlike cascading the deletion onward.
    """
    blocking = {name: count for name, count in blockers.items() if count}
    if not blocking:
        return
    listed = ", ".join(f"{name}: {count}" for name, count in blocking.items())
    raise ValidationError(
        f"Нельзя удалить {entity_name}: на него ссылаются связанные записи ({listed}). "
        "Сначала удалите или перепривяжите их.",
        details={"blocked_by": blocking},
    )


def apply_patch(obj: object, data: dict[str, Any]) -> dict[str, Any]:
    """Assign only the fields present in a PATCH body.

    Returns the subset that actually changed, which keeps both the audit diff
    and the "nothing to do" fast path honest.
    """
    changed: dict[str, Any] = {}
    for field, value in data.items():
        if getattr(obj, field, None) != value:
            setattr(obj, field, value)
            changed[field] = value
    return changed
