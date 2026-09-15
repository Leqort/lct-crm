"""Automatic audit logging (SPEC §5).

Design notes — this is one of the "complex places" the customer asked to see
commented:

* The *who/where* of a request lives in a `contextvars.ContextVar`, set once by
  middleware. Service code never passes an actor around by hand.
* The *what* is derived from SQLAlchemy's own attribute history in an
  `after_flush` session event. `after_flush` is the only hook where primary keys
  are already populated **and** attribute history is still intact, which is
  exactly what an audit record needs.
* Audit rows are written with a Core INSERT on the flush connection inside a
  SAVEPOINT. A failure there rolls back only the savepoint, so a broken audit
  write can never abort the business transaction (SPEC §5.5).
"""

from __future__ import annotations

import datetime as dt
import decimal
import enum
import hashlib
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.enums import AuditAction

logger = logging.getLogger(__name__)

# Never interesting in a diff: maintained by the ORM/DB, not by a human.
# `*_normalized` columns are derived mirrors of their source column, so logging
# them would double every name change.
EXCLUDED_FIELDS = frozenset({"created_at", "updated_at"})
EXCLUDED_SUFFIXES = ("_normalized",)


@dataclass(slots=True)
class AuditContext:
    """Who is acting, from where, within which request."""

    actor_id: uuid.UUID | None = None
    actor_name: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


_EMPTY_CONTEXT = AuditContext()
_audit_context: ContextVar[AuditContext] = ContextVar("audit_context", default=_EMPTY_CONTEXT)


def get_audit_context() -> AuditContext:
    return _audit_context.get()


def set_audit_context(ctx: AuditContext) -> Token[AuditContext]:
    return _audit_context.set(ctx)


def ensure_audit_context() -> AuditContext:
    """Return a per-task context, replacing the shared empty default.

    The default instance is module-global; mutating it would leak one request's
    actor into every other task that never went through the middleware.
    """
    ctx = _audit_context.get()
    if ctx is _EMPTY_CONTEXT:
        ctx = AuditContext()
        _audit_context.set(ctx)
    return ctx


def reset_audit_context(token: Token[AuditContext]) -> None:
    _audit_context.reset(token)


@contextmanager
def audit_context(ctx: AuditContext) -> Iterator[AuditContext]:
    token = set_audit_context(ctx)
    try:
        yield ctx
    finally:
        reset_audit_context(token)


# ---------------------------------------------------------------------------
# Value handling
# ---------------------------------------------------------------------------


def hash_pd(value: Any) -> str | None:
    """Hash a personal-data value so the log proves *that* it changed.

    The plaintext stays in the entity table only (SPEC §5.3). sha256 over the
    stripped, case-folded text: equal values produce equal hashes, which is all
    an auditor needs to answer "did this field actually change?".
    """
    if value is None:
        return None
    text = str(value).strip().casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def jsonify(value: Any) -> Any:
    """Convert a Python value into something `jsonb` accepts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        return [jsonify(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    return str(value)


def _is_audited_field(key: str) -> bool:
    if key in EXCLUDED_FIELDS:
        return False
    return not key.endswith(EXCLUDED_SUFFIXES)


def build_changes(obj: Base) -> dict[str, Any]:
    """Extract `{field: {old, new}}` for the fields that actually changed."""
    state = inspect(obj)
    model = type(obj)
    pd_fields = model.__pd_fields__
    changes: dict[str, Any] = {}

    for attr in state.mapper.column_attrs:
        key = attr.key
        if not _is_audited_field(key):
            continue
        history = state.attrs[key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old == new:
            continue
        if key in pd_fields:
            # Personal data: record the fact and the hashes, never both
            # plaintexts (SPEC §5.3).
            changes[key] = {
                "masked": True,
                "old_sha256": hash_pd(old),
                "new_sha256": hash_pd(new),
            }
        else:
            changes[key] = {"old": jsonify(old), "new": jsonify(new)}
    return changes


def _action_for_update(changes: dict[str, Any]) -> AuditAction:
    """A soft delete is physically an UPDATE; log it as `delete`."""
    deleted_at = changes.get("deleted_at")
    if isinstance(deleted_at, dict) and deleted_at.get("old") is None and deleted_at.get("new"):
        return AuditAction.DELETE
    return AuditAction.UPDATE


def _entry(
    obj: Base, action: AuditAction, changes: dict[str, Any], ctx: AuditContext
) -> dict[str, Any]:
    return {
        "actor_id": ctx.actor_id,
        "actor_name": ctx.actor_name,
        "action": action.value,
        "entity_type": obj.__tablename__,
        "entity_id": getattr(obj, "id", None),
        "changes": changes or None,
        "ip_address": ctx.ip_address,
        "user_agent": ctx.user_agent,
        "request_id": ctx.request_id,
    }


# ---------------------------------------------------------------------------
# Session events
# ---------------------------------------------------------------------------


def _stamp_actor(session: Session, flush_context: Any, instances: Any) -> None:
    """Fill `created_by`/`updated_by` from the request context automatically."""
    ctx = get_audit_context()
    if ctx.actor_id is None:
        return
    for obj in session.new:
        if hasattr(obj, "created_by") and getattr(obj, "created_by", None) is None:
            obj.created_by = ctx.actor_id
        if hasattr(obj, "updated_by") and getattr(obj, "updated_by", None) is None:
            obj.updated_by = ctx.actor_id
    for obj in session.dirty:
        if session.is_modified(obj, include_collections=False) and hasattr(obj, "updated_by"):
            obj.updated_by = ctx.actor_id


def _collect_and_write(session: Session, flush_context: Any) -> None:
    ctx = get_audit_context()
    entries: list[dict[str, Any]] = []

    for obj in session.new:
        if not getattr(type(obj), "__audit__", False):
            continue
        entries.append(_entry(obj, AuditAction.CREATE, build_changes(obj), ctx))

    for obj in session.dirty:
        if not getattr(type(obj), "__audit__", False):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        changes = build_changes(obj)
        if not changes:
            continue
        entries.append(_entry(obj, _action_for_update(changes), changes, ctx))

    for obj in session.deleted:
        if not getattr(type(obj), "__audit__", False):
            continue
        # Hard deletes are forbidden by policy; if one still happens we want a
        # loud trace of it rather than silence.
        entries.append(_entry(obj, AuditAction.DELETE, build_changes(obj), ctx))

    if not entries:
        return

    connection = session.connection()
    savepoint = connection.begin_nested()
    try:
        connection.execute(sa.insert(AuditLog), entries)
        savepoint.commit()
    except Exception:  # pragma: no cover - defensive, see SPEC §5.5
        savepoint.rollback()
        logger.exception("failed to write %d audit entries", len(entries))


def register_audit_listeners() -> None:
    """Idempotently attach the audit listeners to every ORM Session."""
    if not event.contains(Session, "before_flush", _stamp_actor):
        event.listen(Session, "before_flush", _stamp_actor)
    if not event.contains(Session, "after_flush", _collect_and_write):
        event.listen(Session, "after_flush", _collect_and_write)


def build_event(
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
) -> AuditLog:
    """Build a non-ORM audit event (import, export, read_pd, access_denied...).

    Explicit events are added to the session as ordinary ORM objects rather than
    queued: that guarantees they are written even when the request changed
    nothing else, which is precisely the case for `read_pd` and `access_denied`.
    """
    ctx = get_audit_context()
    return AuditLog(
        actor_id=ctx.actor_id,
        actor_name=ctx.actor_name,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=jsonify(changes) if changes else None,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id,
    )
