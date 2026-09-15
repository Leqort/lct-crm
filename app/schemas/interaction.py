"""Schemas for the core `interactions` entity."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.product import DirectionRead, ProductRead
from app.schemas.university import UniversityShort
from app.schemas.user import UserShort


class InteractionCreate(BaseModel):
    university_id: uuid.UUID
    it_direction_id: uuid.UUID | None = None
    it_product_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    contract_number: str | None = Field(default=None, max_length=200, examples=["ДЛ-2026/114"])
    license_signed_at: dt.date | None = Field(default=None, examples=["2026-02-17"])
    license_years: int | None = Field(default=None, ge=1, le=10, examples=[3])
    license_expires_at: dt.date | None = Field(
        default=None,
        description=("Если не задано, вычисляется как «дата подписания + срок действия»"),
    )
    transfer_status: str | None = Field(default=None, max_length=300)
    comment: str | None = None


class InteractionUpdate(BaseModel):
    university_id: uuid.UUID | None = None
    it_direction_id: uuid.UUID | None = None
    it_product_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    contract_number: str | None = Field(default=None, max_length=200)
    license_signed_at: dt.date | None = None
    license_years: int | None = Field(default=None, ge=1, le=10)
    license_expires_at: dt.date | None = None
    transfer_status: str | None = Field(default=None, max_length=300)
    comment: str | None = None


class InteractionRead(ORMModel):
    id: uuid.UUID
    university_id: uuid.UUID
    university: UniversityShort | None = None
    it_direction_id: uuid.UUID | None
    it_direction: DirectionRead | None = None
    it_product_id: uuid.UUID | None
    it_product: ProductRead | None = None
    responsible_user_id: uuid.UUID | None
    responsible_user: UserShort | None = None
    contract_number: str | None
    license_signed_at: dt.date | None
    license_years: int | None
    license_expires_at: dt.date | None
    transfer_status: str | None
    comment: str | None
    # Reserved for SPEC-02; always null until the workflow engine lands.
    workflow_version_id: uuid.UUID | None = None
    current_stage_id: uuid.UUID | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
