"""Schemas for vendors, IT directions and IT products."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300, examples=["Астра"])


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)


class VendorRead(ORMModel):
    id: uuid.UUID
    name: str
    created_at: dt.datetime
    updated_at: dt.datetime


class DirectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300, examples=["DevOps"])
    description: str | None = None
    is_active: bool = True


class DirectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    is_active: bool | None = None


class DirectionRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300, examples=["Astra Linux Special Edition"])
    vendor_id: uuid.UUID | None = None
    description: str | None = None
    is_active: bool = True
    direction_ids: list[uuid.UUID] = Field(
        default_factory=list, description="ИТ-направления, к которым относится продукт"
    )


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    vendor_id: uuid.UUID | None = None
    description: str | None = None
    is_active: bool | None = None
    direction_ids: list[uuid.UUID] | None = None


class ProductRead(ORMModel):
    id: uuid.UUID
    name: str
    vendor_id: uuid.UUID | None
    vendor: VendorRead | None = None
    description: str | None
    is_active: bool
    directions: list[DirectionRead] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime
