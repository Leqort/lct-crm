"""University, its contacts and the KAM assignments that drive row-level access."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, ClassVar

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import DomainBase

if TYPE_CHECKING:
    from app.models.user import User


class University(DomainBase):
    __tablename__ = "universities"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Case/whitespace-folded copy of `name`. Kept as a real column (not an
    # expression index) because the import matcher needs to read it back and
    # because trigram search must run against the same folded form.
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(sa.Text, default=None)
    region: Mapped[str | None] = mapped_column(sa.Text, default=None)
    inn: Mapped[str | None] = mapped_column(sa.Text, default=None)
    external_id: Mapped[str | None] = mapped_column(sa.Text, default=None)
    comment: Mapped[str | None] = mapped_column(sa.Text, default=None)

    contacts: Mapped[list[UniversityContact]] = relationship(
        back_populates="university", lazy="raise", viewonly=True
    )
    assignments: Mapped[list[UniversityAssignment]] = relationship(
        back_populates="university", lazy="raise", viewonly=True
    )

    __table_args__ = (
        # Uniqueness only among live rows: a soft-deleted university must not
        # block re-creating one with the same name.
        sa.Index(
            "uq_universities_name_live",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "uq_universities_name_normalized_live",
            "name_normalized",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "uq_universities_external_id_live",
            "external_id",
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        sa.Index("ix_universities_region", "region"),
        # Trigram index powering the "did you mean ...?" suggestion during import.
        sa.Index(
            "ix_universities_name_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )


class UniversityContact(DomainBase):
    """Contact person on the university side.

    Personal data, deliberately minimised: full name, position, work contacts.
    """

    __tablename__ = "university_contacts"

    __pd_fields__: ClassVar[frozenset[str]] = frozenset({"full_name", "email", "phone"})

    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("universities.id", ondelete="RESTRICT"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position: Mapped[str | None] = mapped_column(sa.Text, default=None)
    email: Mapped[str | None] = mapped_column(sa.Text, default=None)
    phone: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    university: Mapped[University] = relationship(back_populates="contacts", lazy="selectin")

    __table_args__ = (
        sa.Index("ix_university_contacts_university_id", "university_id"),
        sa.Index(
            "uq_university_contacts_person_live",
            "university_id",
            "full_name",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class UniversityAssignment(DomainBase):
    """Which employee (KAM) owns which university, and for which period."""

    __tablename__ = "university_assignments"

    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("universities.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_from: Mapped[dt.date] = mapped_column(sa.Date, nullable=False)
    # NULL upper bound means "still in effect".
    assigned_to: Mapped[dt.date | None] = mapped_column(sa.Date, default=None)

    university: Mapped[University] = relationship(back_populates="assignments", lazy="selectin")
    user: Mapped[User] = relationship(lazy="selectin")

    __table_args__ = (
        sa.CheckConstraint(
            "assigned_to IS NULL OR assigned_to >= assigned_from",
            name="assignment_period_valid",
        ),
        sa.Index("ix_university_assignments_university_id", "university_id"),
        sa.Index("ix_university_assignments_user_id", "user_id"),
        # A university cannot have two overlapping live assignments. Enforced in
        # the service layer for a friendly message *and* here, so a concurrent
        # request cannot slip past the service check.
        ExcludeConstraint(
            (sa.literal_column("university_id"), "="),
            (sa.literal_column("daterange(assigned_from, assigned_to, '[]')"), "&&"),
            name="university_assignments_no_overlap",
            using="gist",
            where=sa.text("deleted_at IS NULL"),
        ),
    )
