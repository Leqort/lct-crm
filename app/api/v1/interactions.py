"""Interactions — the core card of the CRM."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.deps import (
    CATALOG_ERRORS,
    READ_ERRORS,
    PaginationDep,
    ScopeDep,
    SearchQuery,
    SessionDep,
    SortQuery,
)
from app.core.security import CurrentUser, require_admin, require_manager
from app.schemas.common import Page
from app.schemas.interaction import InteractionCreate, InteractionRead, InteractionUpdate
from app.services.interactions import InteractionService

router = APIRouter(prefix="/interactions", tags=["Взаимодействия"])


@router.get(
    "",
    response_model=Page[InteractionRead],
    summary="Список взаимодействий",
    description=(
        "Карточки «вуз + ИТ-направление + ИТ-продукт».\n\n"
        "Роль `user` видит только карточки закреплённых за ним вузов. "
        "Поля `workflow_version_id` и `current_stage_id` зарезервированы "
        "под workflow-движок (SPEC-02) и пока всегда `null`."
    ),
    responses=READ_ERRORS,
)
async def list_interactions(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    university_id: uuid.UUID | None = Query(default=None, description="Фильтр по вузу"),
    it_direction_id: uuid.UUID | None = Query(default=None, description="Фильтр по направлению"),
    it_product_id: uuid.UUID | None = Query(default=None, description="Фильтр по продукту"),
    responsible_user_id: uuid.UUID | None = Query(
        default=None, description="Фильтр по ответственному менеджеру"
    ),
    period_from: dt.date | None = Query(
        default=None, description="Начало периода действия лицензии"
    ),
    period_to: dt.date | None = Query(default=None, description="Конец периода действия лицензии"),
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[InteractionRead]:
    items, total = await InteractionService(session, scope).list(
        university_id=university_id,
        it_direction_id=it_direction_id,
        it_product_id=it_product_id,
        responsible_user_id=responsible_user_id,
        period_from=period_from,
        period_to=period_to,
        page=page.page,
        size=page.size,
        search=search,
        sort=sort,
    )
    return Page.build(
        [InteractionRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.get(
    "/{interaction_id}",
    response_model=InteractionRead,
    summary="Карточка взаимодействия",
    description=(
        "Возвращает карточку со всеми связанными справочниками. "
        "Чужая карточка даёт `403 ACCESS_DENIED` и запись в аудите."
    ),
    responses=READ_ERRORS,
)
async def get_interaction(
    interaction_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> InteractionRead:
    interaction = await InteractionService(session, scope).get(interaction_id)
    return InteractionRead.model_validate(interaction)


@router.post(
    "",
    response_model=InteractionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать взаимодействие",
    description=(
        "Доступно ролям `manager` и `admin`.\n\n"
        "Тройка «вуз + направление + продукт» уникальна среди неудалённых записей — "
        "это же правило используется как ключ upsert при импорте. "
        "`license_expires_at` вычисляется из даты подписания и срока лицензии, "
        "если не передан явно."
    ),
    responses=CATALOG_ERRORS,
)
async def create_interaction(
    data: InteractionCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> InteractionRead:
    interaction = await InteractionService(session, scope).create(data)
    return InteractionRead.model_validate(interaction)


@router.patch(
    "/{interaction_id}",
    response_model=InteractionRead,
    summary="Изменить взаимодействие",
    description="Частичное обновление. Доступно ролям `manager` и `admin`.",
    responses=CATALOG_ERRORS,
)
async def update_interaction(
    interaction_id: uuid.UUID,
    data: InteractionUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> InteractionRead:
    interaction = await InteractionService(session, scope).update(interaction_id, data)
    return InteractionRead.model_validate(interaction)


@router.delete(
    "/{interaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить взаимодействие (мягкое удаление)",
    description="Только для роли `admin`. Запись остаётся в БД с проставленным `deleted_at`.",
    responses=CATALOG_ERRORS,
)
async def delete_interaction(
    interaction_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await InteractionService(session, scope).delete(interaction_id)
