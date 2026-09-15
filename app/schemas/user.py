"""Schemas for the local user projection."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.models.enums import UserRole
from app.schemas.common import ORMModel


class UserRead(ORMModel):
    id: uuid.UUID
    keycloak_id: str
    full_name: str
    email: str | None = None
    role: UserRole
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class UserShort(ORMModel):
    """Compact form embedded in other responses."""

    id: uuid.UUID
    full_name: str
    role: UserRole


class UserCreate(ORMModel):
    keycloak_id: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=500)
    email: str | None = Field(default=None, max_length=320)
    role: UserRole = UserRole.USER
    is_active: bool = True
