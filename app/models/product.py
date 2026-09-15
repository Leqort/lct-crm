"""Software vendors, IT directions and IT products (SPEC §4.2)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, DomainBase


class Vendor(DomainBase):
    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.Index(
            "uq_vendors_name_live",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "uq_vendors_name_normalized_live",
            "name_normalized",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class ITDirection(DomainBase):
    __tablename__ = "it_directions"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    __table_args__ = (
        sa.Index(
            "uq_it_directions_name_live",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "uq_it_directions_name_normalized_live",
            "name_normalized",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class ITProductDirection(Base):
    """Many-to-many between products and IT directions.

    Plain association table: no soft delete, no audit — the audited fact is the
    change of the owning product.
    """

    __tablename__ = "it_products_directions"

    it_product_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("it_products.id", ondelete="CASCADE"), primary_key=True
    )
    it_direction_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("it_directions.id", ondelete="RESTRICT"), primary_key=True
    )


class ITProduct(DomainBase):
    __tablename__ = "it_products"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("vendors.id", ondelete="RESTRICT"), default=None
    )
    description: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    vendor: Mapped[Vendor | None] = relationship(lazy="selectin")
    directions: Mapped[list[ITDirection]] = relationship(
        secondary="it_products_directions", lazy="selectin"
    )

    __table_args__ = (
        # `vendor_id` is nullable and NULLs never collide in a unique index, so
        # products without a vendor are disambiguated by COALESCE to a fixed
        # sentinel UUID. Without this, "ПО без вендора" could be created twice.
        sa.Index(
            "uq_it_products_name_vendor_live",
            "name_normalized",
            sa.text("COALESCE(vendor_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index("ix_it_products_vendor_id", "vendor_id"),
        sa.Index(
            "ix_it_products_name_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )
