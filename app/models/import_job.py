"""Import bookkeeping: jobs, per-row results and reusable column presets."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import DomainBase
from app.models.enums import (
    IMPORT_JOB_STATUS_ENUM,
    IMPORT_ROW_STATUS_ENUM,
    IMPORT_TARGET_ENUM,
    ImportJobStatus,
    ImportRowStatus,
    ImportTarget,
)


def _pg_enum(enum_cls: type, name: str) -> sa.Enum:
    return sa.Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class ImportJob(DomainBase):
    __tablename__ = "import_jobs"

    filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # sha256 of the raw bytes — used to warn about a repeated upload (SPEC §6.4).
    file_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target: Mapped[ImportTarget] = mapped_column(
        _pg_enum(ImportTarget, IMPORT_TARGET_ENUM), nullable=False
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        _pg_enum(ImportJobStatus, IMPORT_JOB_STATUS_ENUM),
        nullable=False,
        default=ImportJobStatus.PENDING,
    )
    # {excel column title -> target field path}, e.g. {"Вендор": "vendors.name"}
    mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    # Headers of the first row plus the auto-suggested mapping, so the UI can
    # rebuild the mapping screen after a page reload.
    source_headers: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    error_message: Mapped[str | None] = mapped_column(sa.Text, default=None)
    committed_at: Mapped[dt.datetime | None] = mapped_column(default=None)

    rows: Mapped[list[ImportRow]] = relationship(back_populates="job", lazy="raise", viewonly=True)

    __table_args__ = (
        sa.Index("ix_import_jobs_file_hash", "file_hash"),
        sa.Index("ix_import_jobs_status", "status"),
        sa.Index("ix_import_jobs_created_at", "created_at"),
    )


class ImportRow(DomainBase):
    """One parsed spreadsheet row and what validation made of it.

    Not audited: a 10k-row file would otherwise produce 10k audit entries. The
    import itself is audited once, as a single `import` action (SPEC §6.4).
    """

    __tablename__ = "import_rows"
    __audit__ = False

    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Normalised/typed values produced by the validator; the commit step writes
    # from here, never from `raw_data`.
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ImportRowStatus] = mapped_column(
        _pg_enum(ImportRowStatus, IMPORT_ROW_STATUS_ENUM),
        nullable=False,
        default=ImportRowStatus.OK,
    )
    messages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    resolved_entity_id: Mapped[uuid.UUID | None] = mapped_column(default=None)

    job: Mapped[ImportJob] = relationship(back_populates="rows", lazy="raise")

    __table_args__ = (
        sa.UniqueConstraint("job_id", "row_number", name="uq_import_rows_job_id_row_number"),
        sa.Index("ix_import_rows_job_id_status", "job_id", "status"),
    )


class ImportMappingPreset(DomainBase):
    """Saved column mapping so the manager does not remap the same file monthly."""

    __tablename__ = "import_mapping_presets"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target: Mapped[ImportTarget] = mapped_column(
        _pg_enum(ImportTarget, IMPORT_TARGET_ENUM), nullable=False
    )
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        sa.Index(
            "uq_import_mapping_presets_name_target_live",
            "name",
            "target",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
