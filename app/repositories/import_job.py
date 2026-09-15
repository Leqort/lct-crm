"""Repositories for import jobs, parsed rows and mapping presets."""

from __future__ import annotations

import uuid
from typing import ClassVar

import sqlalchemy as sa

from app.models.enums import ImportRowStatus
from app.models.import_job import ImportJob, ImportMappingPreset, ImportRow
from app.repositories.base import BaseRepository


class ImportJobRepository(BaseRepository[ImportJob]):
    model = ImportJob
    searchable_fields: ClassVar[tuple[str, ...]] = ("filename",)
    sortable_fields: ClassVar[tuple[str, ...]] = ("created_at", "committed_at", "filename")
    default_order: ClassVar[tuple[str, ...]] = ("-created_at",)

    async def find_previous_with_hash(
        self, file_hash: str, *, exclude_id: uuid.UUID | None = None
    ) -> ImportJob | None:
        """Detect a re-upload of an identical file (SPEC §6 step 4)."""
        conditions = [ImportJob.file_hash == file_hash, ImportJob.deleted_at.is_(None)]
        if exclude_id is not None:
            conditions.append(ImportJob.id != exclude_id)
        return await self.session.scalar(
            sa.select(ImportJob).where(*conditions).order_by(ImportJob.created_at.desc())
        )


class ImportRowRepository(BaseRepository[ImportRow]):
    model = ImportRow
    sortable_fields: ClassVar[tuple[str, ...]] = ("row_number",)
    default_order: ClassVar[tuple[str, ...]] = ("row_number",)

    async def list_for_job(
        self,
        job_id: uuid.UUID,
        *,
        status: ImportRowStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[ImportRow], int]:
        filters = [ImportRow.job_id == job_id]
        if status is not None:
            filters.append(ImportRow.status == status)
        return await self.list(page=page, size=size, extra_filters=filters)

    async def iter_importable(self, job_id: uuid.UUID) -> list[ImportRow]:
        """Rows the commit step may write: everything except hard errors."""
        stmt = (
            sa.select(ImportRow)
            .where(
                ImportRow.job_id == job_id,
                ImportRow.deleted_at.is_(None),
                ImportRow.status != ImportRowStatus.ERROR,
            )
            .order_by(ImportRow.row_number)
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_by_status(self, job_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            sa.select(ImportRow.status, sa.func.count())
            .where(ImportRow.job_id == job_id, ImportRow.deleted_at.is_(None))
            .group_by(ImportRow.status)
        )
        result = await self.session.execute(stmt)
        return {str(status.value): int(count) for status, count in result.all()}


class ImportMappingPresetRepository(BaseRepository[ImportMappingPreset]):
    model = ImportMappingPreset
    searchable_fields: ClassVar[tuple[str, ...]] = ("name",)
    sortable_fields: ClassVar[tuple[str, ...]] = ("name", "created_at")
    default_order: ClassVar[tuple[str, ...]] = ("name",)
