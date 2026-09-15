"""Data access for the workflow engine."""

from __future__ import annotations

import uuid
from typing import ClassVar

import sqlalchemy as sa

from app.models.enums import WorkflowVersionStatus
from app.models.workflow import (
    InteractionStageHistory,
    Workflow,
    WorkflowAttachment,
    WorkflowAttachmentBlob,
    WorkflowStage,
    WorkflowTransition,
    WorkflowVersion,
)
from app.repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[Workflow]):
    model = Workflow
    searchable_fields: ClassVar[tuple[str, ...]] = ("name", "description")
    sortable_fields: ClassVar[tuple[str, ...]] = ("name", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("name",)

    async def find_by_normalized_name(self, normalized: str) -> Workflow | None:
        return await self.session.scalar(
            sa.select(Workflow).where(
                Workflow.name_normalized == normalized, Workflow.deleted_at.is_(None)
            )
        )

    async def find_default(self) -> Workflow | None:
        """The workflow new cards are started on."""
        return await self.session.scalar(
            sa.select(Workflow).where(
                Workflow.is_default.is_(True),
                Workflow.is_active.is_(True),
                Workflow.deleted_at.is_(None),
            )
        )

    async def clear_default(self, *, except_id: uuid.UUID | None = None) -> None:
        """Demote the current default so a new one can take its place."""
        stmt = sa.select(Workflow).where(
            Workflow.is_default.is_(True), Workflow.deleted_at.is_(None)
        )
        if except_id is not None:
            stmt = stmt.where(Workflow.id != except_id)
        for workflow in (await self.session.scalars(stmt)).all():
            workflow.is_default = False
        await self.session.flush()


class WorkflowVersionRepository(BaseRepository[WorkflowVersion]):
    model = WorkflowVersion
    sortable_fields: ClassVar[tuple[str, ...]] = ("version", "created_at", "published_at")
    default_order: ClassVar[tuple[str, ...]] = ("-version",)

    async def find_published(self, workflow_id: uuid.UUID) -> WorkflowVersion | None:
        return await self.session.scalar(
            sa.select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.status == WorkflowVersionStatus.PUBLISHED,
                WorkflowVersion.deleted_at.is_(None),
            )
        )

    async def list_for_workflow(self, workflow_id: uuid.UUID) -> list[WorkflowVersion]:
        return list(
            (
                await self.session.scalars(
                    sa.select(WorkflowVersion)
                    .where(
                        WorkflowVersion.workflow_id == workflow_id,
                        WorkflowVersion.deleted_at.is_(None),
                    )
                    .order_by(WorkflowVersion.version.desc())
                )
            ).all()
        )

    async def next_version_number(self, workflow_id: uuid.UUID) -> int:
        """Version numbers never reuse a value, even after a draft is deleted."""
        highest = await self.session.scalar(
            sa.select(sa.func.max(WorkflowVersion.version)).where(
                WorkflowVersion.workflow_id == workflow_id
            )
        )
        return int(highest or 0) + 1

    async def count_cards_on_version(self, version_id: uuid.UUID) -> int:
        from app.models.interaction import Interaction

        total = await self.session.scalar(
            sa.select(sa.func.count())
            .select_from(Interaction)
            .where(
                Interaction.workflow_version_id == version_id,
                Interaction.deleted_at.is_(None),
            )
        )
        return int(total or 0)


class WorkflowStageRepository(BaseRepository[WorkflowStage]):
    model = WorkflowStage
    searchable_fields: ClassVar[tuple[str, ...]] = ("name", "code", "description")
    sortable_fields: ClassVar[tuple[str, ...]] = ("order_index", "name", "created_at")
    default_order: ClassVar[tuple[str, ...]] = ("order_index",)

    async def list_for_version(self, version_id: uuid.UUID) -> list[WorkflowStage]:
        return list(
            (
                await self.session.scalars(
                    sa.select(WorkflowStage)
                    .where(
                        WorkflowStage.workflow_version_id == version_id,
                        WorkflowStage.deleted_at.is_(None),
                    )
                    .order_by(WorkflowStage.order_index, WorkflowStage.created_at)
                )
            ).all()
        )

    async def find_initial(self, version_id: uuid.UUID) -> WorkflowStage | None:
        return await self.session.scalar(
            sa.select(WorkflowStage).where(
                WorkflowStage.workflow_version_id == version_id,
                WorkflowStage.is_initial.is_(True),
                WorkflowStage.deleted_at.is_(None),
            )
        )

    async def max_order_index(self, version_id: uuid.UUID) -> int:
        highest = await self.session.scalar(
            sa.select(sa.func.max(WorkflowStage.order_index)).where(
                WorkflowStage.workflow_version_id == version_id,
                WorkflowStage.deleted_at.is_(None),
            )
        )
        return int(highest or 0)

    async def count_cards_on_stage(self, stage_id: uuid.UUID) -> int:
        from app.models.interaction import Interaction

        total = await self.session.scalar(
            sa.select(sa.func.count())
            .select_from(Interaction)
            .where(Interaction.current_stage_id == stage_id, Interaction.deleted_at.is_(None))
        )
        return int(total or 0)

    async def count_cards_per_stage(self, version_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """How many live cards sit on each stage — the graph shows this."""
        from app.models.interaction import Interaction

        rows = await self.session.execute(
            sa.select(Interaction.current_stage_id, sa.func.count())
            .where(
                Interaction.workflow_version_id == version_id,
                Interaction.current_stage_id.is_not(None),
                Interaction.deleted_at.is_(None),
            )
            .group_by(Interaction.current_stage_id)
        )
        return {stage_id: int(count) for stage_id, count in rows.all() if stage_id}


class WorkflowTransitionRepository(BaseRepository[WorkflowTransition]):
    model = WorkflowTransition
    sortable_fields: ClassVar[tuple[str, ...]] = ("created_at",)
    default_order: ClassVar[tuple[str, ...]] = ("created_at",)

    async def list_for_version(self, version_id: uuid.UUID) -> list[WorkflowTransition]:
        return list(
            (
                await self.session.scalars(
                    sa.select(WorkflowTransition).where(
                        WorkflowTransition.workflow_version_id == version_id,
                        WorkflowTransition.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    async def list_from_stage(self, stage_id: uuid.UUID) -> list[WorkflowTransition]:
        """Moves available from one stage — what the UI offers as buttons."""
        return list(
            (
                await self.session.scalars(
                    sa.select(WorkflowTransition).where(
                        WorkflowTransition.from_stage_id == stage_id,
                        WorkflowTransition.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    async def find_pair(
        self, version_id: uuid.UUID, from_stage_id: uuid.UUID, to_stage_id: uuid.UUID
    ) -> WorkflowTransition | None:
        return await self.session.scalar(
            sa.select(WorkflowTransition).where(
                WorkflowTransition.workflow_version_id == version_id,
                WorkflowTransition.from_stage_id == from_stage_id,
                WorkflowTransition.to_stage_id == to_stage_id,
                WorkflowTransition.deleted_at.is_(None),
            )
        )


class InteractionStageHistoryRepository(BaseRepository[InteractionStageHistory]):
    model = InteractionStageHistory
    sortable_fields: ClassVar[tuple[str, ...]] = ("created_at",)
    default_order: ClassVar[tuple[str, ...]] = ("created_at",)
    university_scope_column: ClassVar[str | None] = "university_id"

    async def list_for_interaction(
        self, interaction_id: uuid.UUID
    ) -> list[InteractionStageHistory]:
        return list(
            (
                await self.session.scalars(
                    sa.select(InteractionStageHistory)
                    .where(
                        InteractionStageHistory.interaction_id == interaction_id,
                        InteractionStageHistory.deleted_at.is_(None),
                    )
                    .order_by(InteractionStageHistory.created_at, InteractionStageHistory.id)
                )
            ).all()
        )


class WorkflowAttachmentRepository(BaseRepository[WorkflowAttachment]):
    model = WorkflowAttachment
    searchable_fields: ClassVar[tuple[str, ...]] = ("filename", "comment")
    sortable_fields: ClassVar[tuple[str, ...]] = ("filename", "created_at", "size_bytes")
    default_order: ClassVar[tuple[str, ...]] = ("-created_at",)
    university_scope_column: ClassVar[str | None] = "university_id"

    async def store_blob(self, attachment_id: uuid.UUID, data: bytes) -> None:
        self.session.add(WorkflowAttachmentBlob(attachment_id=attachment_id, data=data))
        await self.session.flush()

    async def read_blob(self, attachment_id: uuid.UUID) -> bytes | None:
        return await self.session.scalar(
            sa.select(WorkflowAttachmentBlob.data).where(
                WorkflowAttachmentBlob.attachment_id == attachment_id
            )
        )
