"""Read-only schemas for the audit trail."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import IPvAnyAddress

from app.models.enums import AuditAction
from app.schemas.common import ORMModel


class AuditLogRead(ORMModel):
    id: int
    occurred_at: dt.datetime
    actor_id: uuid.UUID | None
    actor_name: str | None
    action: AuditAction
    entity_type: str
    entity_id: uuid.UUID | None
    changes: dict[str, Any] | None
    # asyncpg hands `inet` back as an ip address object, never a plain string.
    ip_address: IPvAnyAddress | None
    user_agent: str | None
    request_id: uuid.UUID | None
