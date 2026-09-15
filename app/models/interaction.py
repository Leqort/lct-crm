"""`interactions` — the core card that SPEC-02's workflow engine will drive."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import DomainBase
from app.models.product import ITDirection, ITProduct
from app.models.university import University
from app.models.user import User
from app.models.workflow import WorkflowStage, WorkflowVersion

# Sentinel used in the partial unique index below to make NULL direction/product
# behave like a value: in PostgreSQL two NULLs are never "equal", so without it
# the business key (university, direction, product) would not prevent duplicates
# for rows that carry no direction or no product — exactly the rows the customer
# catalog is full of.
NULL_UUID_SENTINEL = "00000000-0000-0000-0000-000000000000"


class Interaction(DomainBase):
    __tablename__ = "interactions"

    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("universities.id", ondelete="RESTRICT"), nullable=False
    )
    it_direction_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("it_directions.id", ondelete="RESTRICT"), default=None
    )
    it_product_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("it_products.id", ondelete="RESTRICT"), default=None
    )
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="RESTRICT"), default=None
    )

    contract_number: Mapped[str | None] = mapped_column(sa.Text, default=None)
    license_signed_at: Mapped[dt.date | None] = mapped_column(sa.Date, default=None)
    license_years: Mapped[int | None] = mapped_column(sa.SmallInteger, default=None)
    # Derived from the two fields above whenever both are known; kept as a stored
    # column (not computed) because imports may deliver an explicit expiry later.
    license_expires_at: Mapped[dt.date | None] = mapped_column(sa.Date, default=None)
    transfer_status: Mapped[str | None] = mapped_column(sa.Text, default=None)
    comment: Mapped[str | None] = mapped_column(sa.Text, default=None)

    # --- Workflow (SPEC-02) -----------------------------------------------
    # The version the card started on, not the workflow: publishing a new
    # version must not reroute work already in flight (A6). Both stay nullable —
    # a card can exist before any workflow is configured, and the import creates
    # exactly such cards.
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_versions.id", ondelete="RESTRICT"), default=None
    )
    current_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="RESTRICT"), default=None
    )

    university: Mapped[University] = relationship(lazy="selectin")
    it_direction: Mapped[ITDirection | None] = relationship(lazy="selectin")
    it_product: Mapped[ITProduct | None] = relationship(lazy="selectin")
    responsible_user: Mapped[User | None] = relationship(lazy="selectin")
    current_stage: Mapped[WorkflowStage | None] = relationship(lazy="selectin")
    workflow_version: Mapped[WorkflowVersion | None] = relationship(lazy="selectin")

    __table_args__ = (
        sa.CheckConstraint(
            "license_years IS NULL OR (license_years BETWEEN 1 AND 10)",
            name="license_years_range",
        ),
        # Business key for import upsert (SPEC §4.3 / §6 step 4).
        sa.Index(
            "uq_interactions_business_key_live",
            "university_id",
            sa.text(f"COALESCE(it_direction_id, '{NULL_UUID_SENTINEL}'::uuid)"),
            sa.text(f"COALESCE(it_product_id, '{NULL_UUID_SENTINEL}'::uuid)"),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index("ix_interactions_university_id", "university_id"),
        sa.Index("ix_interactions_responsible_user_id", "responsible_user_id"),
        sa.Index("ix_interactions_license_expires_at", "license_expires_at"),
        sa.Index("ix_interactions_university_id_it_product_id", "university_id", "it_product_id"),
        # Reporting (SPEC §9): filters by period, direction and product.
        sa.Index("ix_interactions_it_direction_id", "it_direction_id"),
        sa.Index("ix_interactions_it_product_id", "it_product_id"),
        sa.Index("ix_interactions_license_signed_at", "license_signed_at"),
        # Workflow filtering: "все карточки, стоящие на этапе X" (FR-01).
        sa.Index("ix_interactions_current_stage_id", "current_stage_id"),
        sa.Index("ix_interactions_workflow_version_id", "workflow_version_id"),
    )
