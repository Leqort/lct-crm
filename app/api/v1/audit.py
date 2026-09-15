"""Audit trail API — read-only, admin only (SPEC §5.6, §7)."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import PaginationDep, ScopeDep, SessionDep, error_responses
from app.core.errors import ErrorCode
from app.core.security import CurrentUser, require_admin
from app.models.enums import AuditAction
from app.schemas.audit import AuditLogRead
from app.schemas.common import Page
from app.services.audit import search_audit_log

router = APIRouter(prefix="/audit", tags=["Аудит"])

AUDIT_ERRORS = error_responses(
    403, 422, **{"403": ErrorCode.ACCESS_DENIED.value, "422": ErrorCode.VALIDATION_ERROR.value}
)


@router.get(
    "",
    response_model=Page[AuditLogRead],
    summary="Журнал аудита",
    description=(
        "Только для роли `admin`. Журнал доступен исключительно на чтение: "
        "таблица `audit_log` защищена от UPDATE и DELETE на уровне БД.\n\n"
        "В поле `changes` хранятся только реально изменившиеся поля в виде "
        "`{поле: {old, new}}`. Персональные данные (`full_name`, `email`, `phone`) "
        "маскируются: сохраняется факт изменения и sha256-хеши значений, "
        "но не открытый текст."
    ),
    responses=AUDIT_ERRORS,
)
async def list_audit(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    actor_id: uuid.UUID | None = Query(default=None, description="Кто выполнил действие"),
    entity_type: str | None = Query(
        default=None, description="Имя таблицы сущности", examples=["universities"]
    ),
    entity_id: uuid.UUID | None = Query(default=None, description="Идентификатор сущности"),
    action: AuditAction | None = Query(default=None, description="Тип действия"),
    date_from: dt.datetime | None = Query(
        default=None, description="Начало периода (включительно)"
    ),
    date_to: dt.datetime | None = Query(default=None, description="Конец периода (включительно)"),
    _: CurrentUser = Depends(require_admin),
) -> Page[AuditLogRead]:
    items, total = await search_audit_log(
        session,
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
        page=page.page,
        size=page.size,
    )
    return Page.build(
        [AuditLogRead.model_validate(item) for item in items], total, page.page, page.size
    )
