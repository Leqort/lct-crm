"""Read access to the local user projection plus the caller's own identity."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    CATALOG_ERRORS,
    READ_ERRORS,
    CurrentUserDep,
    PaginationDep,
    ScopeDep,
    SearchQuery,
    SessionDep,
    SortQuery,
)
from app.core.security import CurrentUser, require_admin
from app.schemas.common import Page
from app.schemas.user import UserCreate, UserRead
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["Сотрудники"])


@router.get(
    "/me",
    response_model=CurrentUser,
    summary="Текущий пользователь",
    description=(
        "Возвращает личность, с которой работает запрос.\n\n"
        "В режиме `AUTH_MODE=dev` она берётся из заголовка `X-Debug-User` "
        "(UUID, `keycloak_id` или email существующего сотрудника), "
        "в режиме `keycloak` — из проверенного JWT."
    ),
    responses=READ_ERRORS,
)
async def get_me(user: CurrentUserDep) -> CurrentUser:
    return user


@router.get(
    "",
    response_model=Page[UserRead],
    summary="Список сотрудников Школы",
    description="Локальная проекция пользователей Keycloak. Источник истины — Keycloak.",
    responses=READ_ERRORS,
)
async def list_users(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[UserRead]:
    items, total = await UserService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [UserRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Карточка сотрудника",
    description="Возвращает сотрудника по идентификатору.",
    responses=READ_ERRORS,
)
async def get_user(user_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> UserRead:
    return UserRead.model_validate(await UserService(session, scope).get(user_id))


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Завести сотрудника вручную",
    description=(
        "Только для роли `admin`. Нужен до подключения Keycloak и для сервисных "
        "учётных записей; при работе через Keycloak проекция создаётся автоматически "
        "при первом входе."
    ),
    responses=CATALOG_ERRORS,
)
async def create_user(
    data: UserCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> UserRead:
    return UserRead.model_validate(await UserService(session, scope).create(data))
