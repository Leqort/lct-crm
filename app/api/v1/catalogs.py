"""Vendors, IT directions and IT products — the same CRUD pattern for each."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

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
from app.schemas.product import (
    DirectionCreate,
    DirectionRead,
    DirectionUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    VendorCreate,
    VendorRead,
    VendorUpdate,
)
from app.services.catalogs import ITDirectionService, ITProductService, VendorService

vendors_router = APIRouter(prefix="/vendors", tags=["Справочник: вендоры"])
directions_router = APIRouter(prefix="/it-directions", tags=["Справочник: ИТ-направления"])
products_router = APIRouter(prefix="/it-products", tags=["Справочник: ИТ-продукты"])


# --- vendors ---------------------------------------------------------------


@vendors_router.get(
    "",
    response_model=Page[VendorRead],
    summary="Список вендоров",
    description="Справочник вендоров ПО. Доступен всем ролям.",
    responses=READ_ERRORS,
)
async def list_vendors(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[VendorRead]:
    items, total = await VendorService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [VendorRead.model_validate(item) for item in items], total, page.page, page.size
    )


@vendors_router.get(
    "/{vendor_id}",
    response_model=VendorRead,
    summary="Карточка вендора",
    description="Возвращает вендора по идентификатору.",
    responses=READ_ERRORS,
)
async def get_vendor(vendor_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> VendorRead:
    return VendorRead.model_validate(await VendorService(session, scope).get(vendor_id))


@vendors_router.post(
    "",
    response_model=VendorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать вендора",
    description="Доступно ролям `manager` и `admin`. Название уникально.",
    responses=CATALOG_ERRORS,
)
async def create_vendor(
    data: VendorCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> VendorRead:
    return VendorRead.model_validate(await VendorService(session, scope).create(data))


@vendors_router.patch(
    "/{vendor_id}",
    response_model=VendorRead,
    summary="Изменить вендора",
    description="Частичное обновление. Доступно ролям `manager` и `admin`.",
    responses=CATALOG_ERRORS,
)
async def update_vendor(
    vendor_id: uuid.UUID,
    data: VendorUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> VendorRead:
    return VendorRead.model_validate(await VendorService(session, scope).update(vendor_id, data))


@vendors_router.delete(
    "/{vendor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить вендора (мягкое удаление)",
    description="Только для роли `admin`.",
    responses=CATALOG_ERRORS,
)
async def delete_vendor(
    vendor_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await VendorService(session, scope).delete(vendor_id)


# --- IT directions ---------------------------------------------------------


@directions_router.get(
    "",
    response_model=Page[DirectionRead],
    summary="Список ИТ-направлений",
    description="Справочник направлений: DevOps, QA, Data Science и т.д.",
    responses=READ_ERRORS,
)
async def list_directions(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[DirectionRead]:
    items, total = await ITDirectionService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [DirectionRead.model_validate(item) for item in items], total, page.page, page.size
    )


@directions_router.get(
    "/{direction_id}",
    response_model=DirectionRead,
    summary="Карточка ИТ-направления",
    description="Возвращает направление по идентификатору.",
    responses=READ_ERRORS,
)
async def get_direction(
    direction_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> DirectionRead:
    return DirectionRead.model_validate(await ITDirectionService(session, scope).get(direction_id))


@directions_router.post(
    "",
    response_model=DirectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать ИТ-направление",
    description="Доступно ролям `manager` и `admin`.",
    responses=CATALOG_ERRORS,
)
async def create_direction(
    data: DirectionCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> DirectionRead:
    return DirectionRead.model_validate(await ITDirectionService(session, scope).create(data))


@directions_router.patch(
    "/{direction_id}",
    response_model=DirectionRead,
    summary="Изменить ИТ-направление",
    description="Частичное обновление. Доступно ролям `manager` и `admin`.",
    responses=CATALOG_ERRORS,
)
async def update_direction(
    direction_id: uuid.UUID,
    data: DirectionUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> DirectionRead:
    return DirectionRead.model_validate(
        await ITDirectionService(session, scope).update(direction_id, data)
    )


@directions_router.delete(
    "/{direction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить ИТ-направление (мягкое удаление)",
    description="Только для роли `admin`.",
    responses=CATALOG_ERRORS,
)
async def delete_direction(
    direction_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await ITDirectionService(session, scope).delete(direction_id)


# --- IT products -----------------------------------------------------------


@products_router.get(
    "",
    response_model=Page[ProductRead],
    summary="Список ИТ-продуктов",
    description="Справочник ПО и лицензий. Название уникально в паре с вендором.",
    responses=READ_ERRORS,
)
async def list_products(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[ProductRead]:
    items, total = await ITProductService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [ProductRead.model_validate(item) for item in items], total, page.page, page.size
    )


@products_router.get(
    "/{product_id}",
    response_model=ProductRead,
    summary="Карточка ИТ-продукта",
    description="Возвращает продукт вместе с вендором и привязанными направлениями.",
    responses=READ_ERRORS,
)
async def get_product(product_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> ProductRead:
    return ProductRead.model_validate(await ITProductService(session, scope).get(product_id))


@products_router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать ИТ-продукт",
    description=(
        "Доступно ролям `manager` и `admin`. Пара «название + вендор» уникальна "
        "среди неудалённых записей."
    ),
    responses=CATALOG_ERRORS,
)
async def create_product(
    data: ProductCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ProductRead:
    return ProductRead.model_validate(await ITProductService(session, scope).create(data))


@products_router.patch(
    "/{product_id}",
    response_model=ProductRead,
    summary="Изменить ИТ-продукт",
    description=(
        "Частичное обновление. Передача `direction_ids` полностью заменяет "
        "набор привязанных направлений."
    ),
    responses=CATALOG_ERRORS,
)
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ProductRead:
    return ProductRead.model_validate(
        await ITProductService(session, scope).update(product_id, data)
    )


@products_router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить ИТ-продукт (мягкое удаление)",
    description="Только для роли `admin`.",
    responses=CATALOG_ERRORS,
)
async def delete_product(
    product_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await ITProductService(session, scope).delete(product_id)
