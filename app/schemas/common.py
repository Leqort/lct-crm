"""Shared response envelopes and query-parameter models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[ItemT](BaseModel):
    """Standard list envelope used by every collection endpoint."""

    items: list[ItemT]
    total: int = Field(description="Общее число записей, удовлетворяющих фильтру")
    page: int = Field(description="Номер текущей страницы, начиная с 1")
    size: int = Field(description="Размер страницы")
    pages: int = Field(description="Всего страниц")

    @classmethod
    def build(cls, items: list[ItemT], total: int, page: int, size: int) -> Page[ItemT]:
        pages = (total + size - 1) // size if size else 0
        return cls(items=items, total=total, page=page, size=size, pages=pages)


class ErrorBody(BaseModel):
    code: str = Field(examples=["VALIDATION_ERROR"])
    message: str = Field(examples=["Переданные данные не прошли проверку"])
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, examples=["0d2f8f3e-1c1a-4a1e-9f2f-6f0b7f1f2a3b"])


class ErrorResponse(BaseModel):
    """Single error contract for the whole API (SPEC §10)."""

    error: ErrorBody
