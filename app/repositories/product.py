"""Repositories for vendors, IT directions and IT products."""

from __future__ import annotations

import uuid
from typing import ClassVar

import sqlalchemy as sa

from app.models.product import ITDirection, ITProduct, Vendor
from app.repositories.base import BaseRepository


class VendorRepository(BaseRepository[Vendor]):
    model = Vendor
    searchable_fields: ClassVar[tuple[str, ...]] = ("name",)
    sortable_fields: ClassVar[tuple[str, ...]] = ("name", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("name",)

    async def find_by_normalized_name(self, normalized: str) -> Vendor | None:
        stmt = sa.select(Vendor).where(
            Vendor.name_normalized == normalized, Vendor.deleted_at.is_(None)
        )
        return await self.session.scalar(stmt)


class ITDirectionRepository(BaseRepository[ITDirection]):
    model = ITDirection
    searchable_fields: ClassVar[tuple[str, ...]] = ("name", "description")
    sortable_fields: ClassVar[tuple[str, ...]] = ("name", "is_active", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("name",)

    async def find_by_normalized_name(self, normalized: str) -> ITDirection | None:
        stmt = sa.select(ITDirection).where(
            ITDirection.name_normalized == normalized, ITDirection.deleted_at.is_(None)
        )
        return await self.session.scalar(stmt)


class ITProductRepository(BaseRepository[ITProduct]):
    model = ITProduct
    searchable_fields: ClassVar[tuple[str, ...]] = ("name", "description")
    sortable_fields: ClassVar[tuple[str, ...]] = ("name", "is_active", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("name",)

    async def find_by_name_and_vendor(
        self, normalized: str, vendor_id: uuid.UUID | None
    ) -> ITProduct | None:
        """Product identity is (name, vendor) — see the partial unique index."""
        vendor_clause = (
            ITProduct.vendor_id.is_(None) if vendor_id is None else ITProduct.vendor_id == vendor_id
        )
        stmt = sa.select(ITProduct).where(
            ITProduct.name_normalized == normalized,
            vendor_clause,
            ITProduct.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt)
