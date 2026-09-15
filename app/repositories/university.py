"""Repositories for universities, their contacts and KAM assignments."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

import sqlalchemy as sa

from app.models.university import University, UniversityAssignment, UniversityContact
from app.repositories.base import BaseRepository


class UniversityRepository(BaseRepository[University]):
    model = University
    searchable_fields: ClassVar[tuple[str, ...]] = ("name", "short_name", "region", "inn")
    sortable_fields: ClassVar[tuple[str, ...]] = (
        "name",
        "short_name",
        "region",
        "created_at",
        "updated_at",
    )
    default_order: ClassVar[tuple[str, ...]] = ("name",)
    # A university is scoped by its own id.
    university_scope_column: ClassVar[str | None] = "id"

    async def find_by_normalized_name(self, normalized: str) -> University | None:
        """Exact match on the folded name — step 1 of the import matcher."""
        stmt = sa.select(University).where(
            University.name_normalized == normalized,
            University.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt)

    async def find_by_external_id(self, external_id: str) -> University | None:
        stmt = sa.select(University).where(
            University.external_id == external_id,
            University.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt)

    async def find_similar(
        self, normalized: str, *, limit: int = 3, threshold: float = 0.4
    ) -> list[tuple[University, float]]:
        """Trigram-similar universities, best first.

        Backed by the GIN trigram index on `name_normalized`. Used only to
        *suggest* a match during import — the importer never merges on its own
        (SPEC §6 step 3).
        """
        similarity = sa.func.similarity(University.name_normalized, normalized).label("score")
        stmt = (
            sa.select(University, similarity)
            .where(
                University.deleted_at.is_(None),
                similarity >= threshold,
            )
            .order_by(similarity.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], float(row[1])) for row in result.all()]


class UniversityContactRepository(BaseRepository[UniversityContact]):
    model = UniversityContact
    searchable_fields: ClassVar[tuple[str, ...]] = ("full_name", "position", "email", "phone")
    sortable_fields: ClassVar[tuple[str, ...]] = ("full_name", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("full_name",)
    university_scope_column: ClassVar[str | None] = "university_id"

    async def find_by_name(
        self, university_id: uuid.UUID, full_name: str
    ) -> UniversityContact | None:
        stmt = sa.select(UniversityContact).where(
            UniversityContact.university_id == university_id,
            UniversityContact.full_name == full_name,
            UniversityContact.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt)


class UniversityAssignmentRepository(BaseRepository[UniversityAssignment]):
    model = UniversityAssignment
    sortable_fields: ClassVar[tuple[str, ...]] = ("assigned_from", "assigned_to", "created_at")
    default_order: ClassVar[tuple[str, ...]] = ("-assigned_from",)
    university_scope_column: ClassVar[str | None] = "university_id"

    async def find_overlapping(
        self,
        university_id: uuid.UUID,
        assigned_from: dt.date,
        assigned_to: dt.date | None,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> UniversityAssignment | None:
        """Any live assignment of the same university whose period intersects.

        Two closed/half-open ranges [a1, a2] and [b1, b2] (NULL = +infinity)
        overlap iff a1 <= b2 and b1 <= a2.
        """
        conditions = [
            UniversityAssignment.university_id == university_id,
            UniversityAssignment.deleted_at.is_(None),
            sa.or_(
                UniversityAssignment.assigned_to.is_(None),
                UniversityAssignment.assigned_to >= assigned_from,
            ),
        ]
        if assigned_to is not None:
            conditions.append(UniversityAssignment.assigned_from <= assigned_to)
        if exclude_id is not None:
            conditions.append(UniversityAssignment.id != exclude_id)
        return await self.session.scalar(sa.select(UniversityAssignment).where(*conditions))
