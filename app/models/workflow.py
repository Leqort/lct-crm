"""Workflow engine: templates, versions, stages, transitions and card routing.

Design notes — the two decisions everything else follows from:

**Versioning (A6).** A workflow is a template; the thing a card actually runs on
is a *version*. Structural edits (adding or removing a stage, changing which
transitions exist) are legal only while a version is a `draft`. Publishing
freezes it, and a card stores the version id it started on, so editing the
workflow later cannot rewrite the route of work already in flight.

Renaming a stage is the deliberate exception: it is a label, not structure, so
it is allowed in place on a published version (FR-03 requires it) and cannot
break a card, because the stage id the card points at does not change.

**Access scoping.** `interaction_stage_history` and `workflow_attachments` carry
a denormalised `university_id`. They belong to an interaction, and the
repository-level visibility filter (SPEC-01 §8) keys on a university column; the
copy lets that filter apply directly instead of every query joining back through
`interactions`. It also keeps the reporting joins of SPEC-03 cheap.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, DomainBase
from app.models.enums import (
    ATTACHMENT_FORMAT_ENUM,
    WORKFLOW_VERSION_STATUS_ENUM,
    AttachmentFormat,
    WorkflowVersionStatus,
)


class Workflow(DomainBase):
    """A named route template, e.g. the customer's 14-step base workflow."""

    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # The workflow new interactions are started on when nothing else is chosen.
    is_default: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow", lazy="raise", viewonly=True
    )

    __table_args__ = (
        sa.Index(
            "uq_workflows_name_normalized_live",
            "name_normalized",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        # At most one default workflow among live rows.
        sa.Index(
            "uq_workflows_single_default",
            sa.text("(is_default)"),
            unique=True,
            postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
        ),
    )


class WorkflowVersion(DomainBase):
    __tablename__ = "workflow_versions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[WorkflowVersionStatus] = mapped_column(
        sa.Enum(
            WorkflowVersionStatus,
            name=WORKFLOW_VERSION_STATUS_ENUM,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=WorkflowVersionStatus.DRAFT,
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), default=None
    )
    comment: Mapped[str | None] = mapped_column(sa.Text, default=None)

    workflow: Mapped[Workflow] = relationship(back_populates="versions", lazy="selectin")
    stages: Mapped[list[WorkflowStage]] = relationship(
        back_populates="workflow_version",
        lazy="raise",
        viewonly=True,
        order_by="WorkflowStage.order_index",
    )
    transitions: Mapped[list[WorkflowTransition]] = relationship(
        back_populates="workflow_version", lazy="raise", viewonly=True
    )

    __table_args__ = (
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
        sa.Index("ix_workflow_versions_workflow_id", "workflow_id"),
        # One published version per workflow: that is the one new cards start on.
        sa.Index(
            "uq_workflow_versions_single_published",
            "workflow_id",
            unique=True,
            postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"),
        ),
    )


class WorkflowStage(DomainBase):
    """One step of a route. `name` is editable even after publishing (FR-03)."""

    __tablename__ = "workflow_stages"

    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    # Stable business code, e.g. WF-01. Optional: user-added stages need none.
    code: Mapped[str | None] = mapped_column(sa.Text, default=None)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # Display order. Deliberately not unique: reordering would otherwise need a
    # deferred constraint or a two-pass shuffle to avoid transient collisions.
    order_index: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    is_initial: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    is_terminal: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # Optional: the stage marks the interaction as successfully completed.
    is_final_success: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    workflow_version: Mapped[WorkflowVersion] = relationship(back_populates="stages", lazy="raise")

    __table_args__ = (
        sa.Index("ix_workflow_stages_workflow_version_id", "workflow_version_id"),
        sa.Index(
            "uq_workflow_stages_code_live",
            "workflow_version_id",
            "code",
            unique=True,
            postgresql_where=sa.text("code IS NOT NULL AND deleted_at IS NULL"),
        ),
        # Exactly one entry point per version; the service creates it, this
        # stops a second one appearing.
        sa.Index(
            "uq_workflow_stages_single_initial",
            "workflow_version_id",
            unique=True,
            postgresql_where=sa.text("is_initial AND deleted_at IS NULL"),
        ),
    )


class WorkflowTransition(DomainBase):
    """An allowed move between two stages of the same version."""

    __tablename__ = "workflow_transitions"

    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    from_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="CASCADE"), nullable=False
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # FR-03 wants a comment when moving between statuses. Per-transition so a
    # workflow can relax it where a comment carries no information.
    requires_comment: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    workflow_version: Mapped[WorkflowVersion] = relationship(
        back_populates="transitions", lazy="raise"
    )
    from_stage: Mapped[WorkflowStage] = relationship(foreign_keys=[from_stage_id], lazy="selectin")
    to_stage: Mapped[WorkflowStage] = relationship(foreign_keys=[to_stage_id], lazy="selectin")

    __table_args__ = (
        sa.CheckConstraint("from_stage_id <> to_stage_id", name="transition_not_self"),
        sa.Index("ix_workflow_transitions_workflow_version_id", "workflow_version_id"),
        sa.Index("ix_workflow_transitions_from_stage_id", "from_stage_id"),
        sa.Index(
            "uq_workflow_transitions_pair_live",
            "workflow_version_id",
            "from_stage_id",
            "to_stage_id",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )


class InteractionStageHistory(DomainBase):
    """One recorded move of a card from stage to stage.

    Append-only in practice: the service never updates a history row. `comment`
    is what FR-03 asks to capture at the moment of the transition.
    """

    __tablename__ = "interaction_stage_history"

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("interactions.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalised for the row-level visibility filter — see the module docstring.
    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("universities.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    # NULL when the card is placed on its first stage.
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="RESTRICT"), default=None
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="RESTRICT"), nullable=False
    )
    transition_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_transitions.id", ondelete="SET NULL"), default=None
    )
    comment: Mapped[str | None] = mapped_column(sa.Text, default=None)

    from_stage: Mapped[WorkflowStage | None] = relationship(
        foreign_keys=[from_stage_id], lazy="selectin"
    )
    to_stage: Mapped[WorkflowStage] = relationship(foreign_keys=[to_stage_id], lazy="selectin")

    __table_args__ = (
        sa.Index("ix_interaction_stage_history_interaction_id", "interaction_id"),
        sa.Index("ix_interaction_stage_history_university_id", "university_id"),
        sa.Index("ix_interaction_stage_history_to_stage_id", "to_stage_id"),
        sa.Index("ix_interaction_stage_history_created_at", "created_at"),
    )


class WorkflowAttachment(DomainBase):
    """A file attached to a specific stage of a specific card (FR-04)."""

    __tablename__ = "workflow_attachments"

    __pd_fields__: ClassVar[frozenset[str]] = frozenset()

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("interactions.id", ondelete="RESTRICT"), nullable=False
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("universities.id", ondelete="RESTRICT"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stages.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_format: Mapped[AttachmentFormat] = mapped_column(
        sa.Enum(
            AttachmentFormat,
            name=ATTACHMENT_FORMAT_ENUM,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    file_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text, default=None)

    stage: Mapped[WorkflowStage] = relationship(lazy="selectin")

    __table_args__ = (
        sa.Index("ix_workflow_attachments_interaction_id", "interaction_id"),
        sa.Index("ix_workflow_attachments_stage_id", "stage_id"),
        sa.Index("ix_workflow_attachments_university_id", "university_id"),
    )


class WorkflowAttachmentBlob(Base):
    """File bytes, kept out of the metadata table.

    Stored in the database rather than on a volume or in object storage: it is
    the conservative option for a system under 152-ФЗ — one place to protect,
    one backup that cannot fall out of sync with its metadata, and no extra
    infrastructure to secure. Split into its own table so listing attachments
    never drags megabytes along.
    """

    __tablename__ = "workflow_attachment_blobs"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_attachments.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
