"""Schemas for the two-phase XLSX import (SPEC §6)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ImportJobStatus, ImportRowStatus, ImportTarget
from app.schemas.common import ORMModel


class ImportStats(BaseModel):
    total: int = 0
    to_create: int = 0
    to_update: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: int = 0
    created: int = 0
    updated: int = 0


class MappingSuggestion(BaseModel):
    column: str = Field(description="Заголовок колонки в файле")
    field: str | None = Field(
        default=None, description="Предложенное поле системы, null — не распознано"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ImportJobRead(ORMModel):
    id: uuid.UUID
    filename: str
    file_hash: str
    target: ImportTarget
    status: ImportJobStatus
    mapping: dict[str, Any]
    stats: dict[str, Any]
    source_headers: list[Any]
    error_message: str | None
    created_by: uuid.UUID | None
    created_at: dt.datetime
    committed_at: dt.datetime | None


class ImportJobCreated(BaseModel):
    """Response of `POST /imports` — everything the mapping screen needs."""

    job: ImportJobRead
    headers: list[str]
    suggested_mapping: list[MappingSuggestion]
    duplicate_of: uuid.UUID | None = Field(
        default=None,
        description="ID предыдущей задачи с тем же файлом. Предупреждение, не блокировка",
    )
    warnings: list[str] = Field(default_factory=list)


class MappingRequest(BaseModel):
    mapping: dict[str, str] = Field(
        description="Соответствие «заголовок колонки файла» → «поле системы»",
        examples=[{"Название ВУЗа": "universities.name", "Вендор": "vendors.name"}],
    )
    save_as_preset: str | None = Field(
        default=None, description="Если задано — маппинг сохраняется под этим именем"
    )


class ImportRowRead(ORMModel):
    id: uuid.UUID
    row_number: int
    status: ImportRowStatus
    raw_data: dict[str, Any]
    parsed_data: dict[str, Any]
    messages: list[Any]
    resolved_entity_id: uuid.UUID | None


class ImportCommitResult(BaseModel):
    job: ImportJobRead
    stats: ImportStats


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Каталог ПО, ежемесячный"])
    target: ImportTarget = ImportTarget.INTERACTIONS
    mapping: dict[str, str]


class PresetRead(ORMModel):
    id: uuid.UUID
    name: str
    target: ImportTarget
    mapping: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: dt.datetime
