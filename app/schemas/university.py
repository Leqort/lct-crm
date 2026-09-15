"""Schemas for universities, contacts and assignments."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel
from app.schemas.user import UserShort


class UniversityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=500, examples=["МГТУ им. Н.Э. Баумана"])
    short_name: str | None = Field(default=None, max_length=200, examples=["Бауманка"])
    region: str | None = Field(default=None, max_length=200, examples=["г. Москва"])
    inn: str | None = Field(default=None, max_length=20, examples=["7701002520"])
    external_id: str | None = Field(
        default=None, max_length=200, description="Идентификатор вуза в LMS/на сайте"
    )
    comment: str | None = None


class UniversityUpdate(BaseModel):
    """PATCH body. Only the fields actually sent are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=500)
    short_name: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    inn: str | None = Field(default=None, max_length=20)
    external_id: str | None = Field(default=None, max_length=200)
    comment: str | None = None


class UniversityRead(ORMModel):
    id: uuid.UUID
    name: str
    short_name: str | None
    region: str | None
    inn: str | None
    external_id: str | None
    comment: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class UniversityShort(ORMModel):
    id: uuid.UUID
    name: str
    short_name: str | None = None


class ContactCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=300, examples=["Иванова Мария Петровна"])
    position: str | None = Field(default=None, max_length=300, examples=["Проректор по ИТ"])
    email: str | None = Field(default=None, max_length=320, examples=["m.ivanova@example.edu"])
    phone: str | None = Field(default=None, max_length=50, examples=["+7 495 000-00-00"])
    is_primary: bool = False


class ContactUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=300)
    position: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    is_primary: bool | None = None


class ContactRead(ORMModel):
    id: uuid.UUID
    university_id: uuid.UUID
    full_name: str
    position: str | None
    email: str | None
    phone: str | None
    is_primary: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class AssignmentCreate(BaseModel):
    user_id: uuid.UUID
    assigned_from: dt.date = Field(examples=["2026-01-01"])
    assigned_to: dt.date | None = Field(
        default=None, description="NULL — назначение действует бессрочно"
    )

    @model_validator(mode="after")
    def _check_period(self) -> AssignmentCreate:
        if self.assigned_to is not None and self.assigned_to < self.assigned_from:
            raise ValueError("Дата окончания не может быть раньше даты начала")
        return self


class AssignmentRead(ORMModel):
    id: uuid.UUID
    university_id: uuid.UUID
    user_id: uuid.UUID
    assigned_from: dt.date
    assigned_to: dt.date | None
    user: UserShort | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
