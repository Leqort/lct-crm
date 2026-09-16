"""Universities, their contacts and KAM assignments."""

from __future__ import annotations

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
from app.schemas.university import (
    AssignmentCreate,
    AssignmentRead,
    ContactCreate,
    ContactRead,
    ContactUpdate,
    UniversityCreate,
    UniversityRead,
    UniversityUpdate,
)
from app.services.assignments import AssignmentService
from app.services.audit import log_read_pd
from app.services.catalogs import UniversityContactService, UniversityService

router = APIRouter(prefix="/universities", tags=["Справочник: вузы"])


@router.get(
    "",
    response_model=Page[UniversityRead],
    summary="Список вузов",
    description=(
        "Постраничный список вузов.\n\n"
        "Роль `user` видит только вузы, на которые он назначен ответственным "
        "в `university_assignments`; `manager` и `admin` видят все. "
        "Фильтрация применяется на уровне репозитория, а не роутера."
    ),
    responses=READ_ERRORS,
)
async def list_universities(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[UniversityRead]:
    items, total = await UniversityService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [UniversityRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.get(
    "/{university_id}",
    response_model=UniversityRead,
    summary="Карточка вуза",
    description=(
        "Возвращает вуз по идентификатору.\n\n"
        "Если вуз существует, но не входит в зону видимости пользователя, "
        "возвращается `403 ACCESS_DENIED` (а не `404`), и факт отказа "
        "фиксируется в аудит-логе."
    ),
    responses=READ_ERRORS,
)
async def get_university(
    university_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> UniversityRead:
    university = await UniversityService(session, scope).get(university_id)
    return UniversityRead.model_validate(university)


@router.post(
    "",
    response_model=UniversityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать вуз",
    description="Доступно ролям `manager` и `admin`. Название уникально среди неудалённых записей.",
    responses=CATALOG_ERRORS,
)
async def create_university(
    data: UniversityCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> UniversityRead:
    university = await UniversityService(session, scope).create(data)
    return UniversityRead.model_validate(university)


@router.patch(
    "/{university_id}",
    response_model=UniversityRead,
    summary="Изменить вуз",
    description=(
        "Частичное обновление: применяются только переданные поля. "
        "Изменения попадают в аудит-лог с точным diff-ом."
    ),
    responses=CATALOG_ERRORS,
)
async def update_university(
    university_id: uuid.UUID,
    data: UniversityUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> UniversityRead:
    university = await UniversityService(session, scope).update(university_id, data)
    return UniversityRead.model_validate(university)


@router.delete(
    "/{university_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить вуз (мягкое удаление)",
    description=(
        "Только для роли `admin`. Запись не удаляется физически: проставляется "
        "`deleted_at`, после чего объект исчезает из всех выборок, но остаётся в БД "
        "для аудита и требований 152-ФЗ."
    ),
    responses=CATALOG_ERRORS,
)
async def delete_university(
    university_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await UniversityService(session, scope).delete(university_id)


# --- contacts --------------------------------------------------------------


@router.get(
    "/{university_id}/contacts",
    response_model=Page[ContactRead],
    summary="Контактные лица вуза",
    description=(
        "Персональные данные. Каждый вызов фиксируется в аудит-логе с действием `read_pd`."
    ),
    responses=READ_ERRORS,
)
async def list_contacts(
    university_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[ContactRead]:
    service = UniversityContactService(session, scope)
    items, total = await service.list(
        university_id=university_id, page=page.page, size=page.size, search=search, sort=sort
    )
    await log_read_pd(
        session,
        entity_type="university_contacts",
        entity_id=university_id,
        returned=len(items),
    )
    await session.commit()
    return Page.build(
        [ContactRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.post(
    "/{university_id}/contacts",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить контактное лицо вуза",
    description=(
        "Хранится минимальный набор ПДн: ФИО, должность, рабочий email и телефон. "
        "В аудит-логе значения этих полей маскируются хешем."
    ),
    responses=CATALOG_ERRORS,
)
async def create_contact(
    university_id: uuid.UUID,
    data: ContactCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ContactRead:
    contact = await UniversityContactService(session, scope).create(university_id, data)
    return ContactRead.model_validate(contact)


# --- assignments -----------------------------------------------------------


@router.get(
    "/{university_id}/assignments",
    response_model=Page[AssignmentRead],
    summary="Закрепление КАМов за вузом",
    description="История и текущие назначения ответственных сотрудников.",
    responses=READ_ERRORS,
)
async def list_assignments(
    university_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    sort: SortQuery = None,
) -> Page[AssignmentRead]:
    items, total = await AssignmentService(session, scope).list_for_university(
        university_id, page=page.page, size=page.size, sort=sort
    )
    return Page.build(
        [AssignmentRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.post(
    "/{university_id}/assignments",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Назначить ответственного за вуз",
    description=(
        "Доступно ролям `manager` и `admin`.\n\n"
        "У вуза не может быть двух активных назначений с пересекающимися периодами: "
        "проверка выполняется в сервисе и дублируется exclusion-ограничением в БД. "
        "`assigned_to = null` означает бессрочное назначение."
    ),
    responses=CATALOG_ERRORS,
)
async def create_assignment(
    university_id: uuid.UUID,
    data: AssignmentCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> AssignmentRead:
    assignment = await AssignmentService(session, scope).create(university_id, data)
    return AssignmentRead.model_validate(assignment)


# --- standalone contact/assignment routes ----------------------------------

contacts_router = APIRouter(prefix="/university-contacts", tags=["Справочник: контакты вузов"])


@contacts_router.get(
    "",
    response_model=Page[ContactRead],
    summary="Список контактных лиц",
    description="Сквозной список контактов по всем доступным пользователю вузам.",
    responses=READ_ERRORS,
)
async def list_all_contacts(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    university_id: uuid.UUID | None = Query(default=None, description="Фильтр по вузу"),
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[ContactRead]:
    service = UniversityContactService(session, scope)
    items, total = await service.list(
        university_id=university_id, page=page.page, size=page.size, search=search, sort=sort
    )
    await log_read_pd(session, entity_type="university_contacts", returned=len(items))
    await session.commit()
    return Page.build(
        [ContactRead.model_validate(item) for item in items], total, page.page, page.size
    )


@contacts_router.get(
    "/{contact_id}",
    response_model=ContactRead,
    summary="Карточка контактного лица",
    description="Чтение ПДн фиксируется в аудит-логе действием `read_pd`.",
    responses=READ_ERRORS,
)
async def get_contact(contact_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> ContactRead:
    contact = await UniversityContactService(session, scope).get(contact_id)
    await log_read_pd(session, entity_type="university_contacts", entity_id=contact_id)
    await session.commit()
    return ContactRead.model_validate(contact)


@contacts_router.patch(
    "/{contact_id}",
    response_model=ContactRead,
    summary="Изменить контактное лицо",
    description="Доступно ролям `manager` и `admin`.",
    responses=CATALOG_ERRORS,
)
async def update_contact(
    contact_id: uuid.UUID,
    data: ContactUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ContactRead:
    contact = await UniversityContactService(session, scope).update(contact_id, data)
    return ContactRead.model_validate(contact)


@contacts_router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить контактное лицо (мягкое удаление)",
    description="Только для роли `admin`.",
    responses=CATALOG_ERRORS,
)
async def delete_contact(
    contact_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await UniversityContactService(session, scope).delete(contact_id)


assignments_router = APIRouter(prefix="/assignments", tags=["Справочник: назначения"])


@assignments_router.delete(
    "/{assignment_id}",
    response_model=AssignmentRead,
    summary="Закрыть назначение",
    description=(
        "Назначение не удаляется: проставляется дата окончания `assigned_to` "
        "(по умолчанию — сегодня), история закрепления сохраняется целиком."
    ),
    responses=CATALOG_ERRORS,
)
async def close_assignment(
    assignment_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> AssignmentRead:
    assignment = await AssignmentService(session, scope).close(assignment_id)
    return AssignmentRead.model_validate(assignment)
