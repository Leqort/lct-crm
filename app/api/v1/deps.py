"""Shared FastAPI dependencies and OpenAPI response helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.schemas.common import ErrorResponse

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


@dataclass(slots=True)
class Pagination:
    page: int
    size: int


def pagination(
    page: int = Query(1, ge=1, description="Номер страницы, начиная с 1"),
    size: int = Query(
        settings.page_size_default,
        ge=1,
        le=settings.page_size_max,
        description=f"Размер страницы, максимум {settings.page_size_max}",
    ),
) -> Pagination:
    return Pagination(page=page, size=size)


PaginationDep = Annotated[Pagination, Depends(pagination)]

SearchQuery = Annotated[
    str | None,
    Query(description="Поиск по названию и другим текстовым полям", examples=["Бауман"]),
]
SortQuery = Annotated[
    str | None,
    Query(
        description="Сортировка: `field` по возрастанию, `-field` по убыванию, через запятую",
        examples=["-created_at", "name"],
    ),
]


async def get_scope(user: CurrentUserDep) -> AccessScope:
    """Row-level visibility derived from the caller's role (SPEC §8)."""
    return AccessScope.from_user(user)


ScopeDep = Annotated[AccessScope, Depends(get_scope)]


_ERROR_DESCRIPTIONS: dict[int, str] = {
    400: "Некорректный запрос",
    403: "Доступ запрещён",
    404: "Объект не найден",
    409: "Конфликт состояния или дубликат",
    413: "Файл слишком большой",
    422: "Ошибка валидации",
    500: "Внутренняя ошибка",
}


def error_responses(*statuses: int, **codes: str) -> dict[int | str, dict[str, Any]]:
    """Build the OpenAPI `responses` block for the documented failure modes.

    Every endpoint lists the concrete error codes it can return — Swagger
    completeness is a separate acceptance item (SPEC §7).
    """
    result: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        description = _ERROR_DESCRIPTIONS.get(status, "Ошибка")
        extra = codes.get(str(status))
        if extra:
            description = f"{description}. Коды: {extra}"
        result[status] = {"model": ErrorResponse, "description": description}
    return result


CATALOG_ERRORS = error_responses(
    403,
    404,
    409,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "404": ErrorCode.NOT_FOUND.value,
        "409": ErrorCode.DUPLICATE_ENTITY.value,
        "422": ErrorCode.VALIDATION_ERROR.value,
    },
)

READ_ERRORS = error_responses(
    403,
    404,
    **{"403": ErrorCode.ACCESS_DENIED.value, "404": ErrorCode.NOT_FOUND.value},
)
