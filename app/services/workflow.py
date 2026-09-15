"""Design-time workflow logic: templates, versions, stages, transitions.

The rule that shapes this module (SPEC-02 A6): a published version is frozen.
Adding or removing a stage, or changing which transitions exist, is only legal
on a `draft`; to change a published route you clone it into a new draft and
publish that. Cards keep the version id they started on, so their route never
changes underneath them.

Renaming a stage is the deliberate exception — FR-03 asks for it explicitly, and
it is safe because the stage id a card points at does not move.
"""

from __future__ import annotations

import builtins
import datetime as dt
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import (
    DuplicateEntityError,
    NotFoundError,
    ValidationError,
    WorkflowVersionLockedError,
)
from app.models.enums import WorkflowVersionStatus
from app.models.workflow import (
    Workflow,
    WorkflowStage,
    WorkflowTransition,
    WorkflowVersion,
)
from app.repositories.workflow import (
    WorkflowRepository,
    WorkflowStageRepository,
    WorkflowTransitionRepository,
    WorkflowVersionRepository,
)
from app.services.base import apply_patch, integrity_guard
from app.services.text import clean_text, normalize_name


class WorkflowService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = WorkflowRepository(session, self.scope)
        self.versions = WorkflowVersionRepository(session, self.scope)
        self.stages = WorkflowStageRepository(session, self.scope)
        self.transitions = WorkflowTransitionRepository(session, self.scope)

    # -- templates ----------------------------------------------------------

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[Workflow], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, workflow_id: uuid.UUID) -> Workflow:
        return await self.repo.get_or_fail(workflow_id)

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        is_default: bool = False,
    ) -> Workflow:
        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название workflow не может быть пустым")
        if await self.repo.find_by_normalized_name(normalize_name(cleaned)):
            raise DuplicateEntityError(
                "Workflow с таким названием уже существует", details={"name": cleaned}
            )
        if is_default:
            await self.repo.clear_default()

        workflow = Workflow(
            name=cleaned,
            name_normalized=normalize_name(cleaned),
            description=clean_text(description),
            is_default=is_default,
        )
        self.repo.add(workflow)
        async with integrity_guard(
            self.session, duplicate_message="Workflow с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return workflow

    async def update(self, workflow_id: uuid.UUID, patch: dict[str, Any]) -> Workflow:
        workflow = await self.repo.get_or_fail(workflow_id)
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название workflow не может быть пустым")
            patch["name"] = name
            patch["name_normalized"] = normalize_name(name)
        if "description" in patch:
            patch["description"] = clean_text(patch["description"])
        if patch.get("is_default"):
            await self.repo.clear_default(except_id=workflow_id)

        apply_patch(workflow, patch)
        async with integrity_guard(
            self.session, duplicate_message="Workflow с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return await self.repo.reload(workflow)

    async def delete(self, workflow_id: uuid.UUID) -> None:
        workflow = await self.repo.get_or_fail(workflow_id)
        published = await self.versions.find_published(workflow_id)
        if published is not None:
            cards = await self.versions.count_cards_on_version(published.id)
            if cards:
                raise ValidationError(
                    f"Нельзя удалить workflow: по нему идут карточки ({cards}). "
                    "Сначала переведите их на другой workflow.",
                    details={"blocked_by": {"карточки": cards}},
                )
        await self.repo.soft_delete(workflow)
        await self.session.commit()

    # -- versions -----------------------------------------------------------

    async def list_versions(self, workflow_id: uuid.UUID) -> builtins.list[WorkflowVersion]:
        await self.repo.get_or_fail(workflow_id)
        return await self.versions.list_for_workflow(workflow_id)

    async def get_version(self, version_id: uuid.UUID) -> WorkflowVersion:
        return await self.versions.get_or_fail(version_id)

    async def create_draft(
        self, workflow_id: uuid.UUID, *, clone_from_id: uuid.UUID | None = None
    ) -> WorkflowVersion:
        """Open a new draft, optionally copying an existing version's structure.

        Cloning is the normal way to change a published route: the copy gets
        fresh stage ids, so editing it cannot touch the cards still running on
        the original.
        """
        await self.repo.get_or_fail(workflow_id)

        source: WorkflowVersion | None = None
        if clone_from_id is not None:
            source = await self.versions.get_or_fail(clone_from_id)
            if source.workflow_id != workflow_id:
                raise ValidationError(
                    "Версия-источник принадлежит другому workflow",
                    details={"version_id": str(clone_from_id)},
                )

        draft = WorkflowVersion(
            workflow_id=workflow_id,
            version=await self.versions.next_version_number(workflow_id),
            status=WorkflowVersionStatus.DRAFT,
        )
        self.versions.add(draft)
        await self.session.flush()

        if source is not None:
            await self._clone_structure(source, draft)

        await self.session.commit()
        return draft

    async def _clone_structure(self, source: WorkflowVersion, target: WorkflowVersion) -> None:
        stage_map: dict[uuid.UUID, uuid.UUID] = {}
        for stage in await self.stages.list_for_version(source.id):
            copy = WorkflowStage(
                workflow_version_id=target.id,
                code=stage.code,
                name=stage.name,
                description=stage.description,
                order_index=stage.order_index,
                is_initial=stage.is_initial,
                is_terminal=stage.is_terminal,
                is_final_success=stage.is_final_success,
            )
            self.stages.add(copy)
            await self.session.flush()
            stage_map[stage.id] = copy.id

        for transition in await self.transitions.list_for_version(source.id):
            self.transitions.add(
                WorkflowTransition(
                    workflow_version_id=target.id,
                    from_stage_id=stage_map[transition.from_stage_id],
                    to_stage_id=stage_map[transition.to_stage_id],
                    name=transition.name,
                    requires_comment=transition.requires_comment,
                )
            )
        await self.session.flush()

    async def publish(self, version_id: uuid.UUID) -> WorkflowVersion:
        """Freeze a draft and make it the route new cards start on."""
        version = await self.versions.get_or_fail(version_id)
        if version.status == WorkflowVersionStatus.PUBLISHED:
            return version
        if version.status == WorkflowVersionStatus.ARCHIVED:
            raise WorkflowVersionLockedError(
                "Версия заархивирована и не может быть опубликована повторно"
            )

        stages = await self.stages.list_for_version(version_id)
        if not stages:
            raise ValidationError(
                "Нельзя опубликовать версию без этапов", details={"version_id": str(version_id)}
            )
        if not any(stage.is_initial for stage in stages):
            raise ValidationError(
                "Не задан начальный этап версии", details={"version_id": str(version_id)}
            )

        # Only one version of a workflow is published at a time; the previous
        # one is archived, not deleted — cards still point at it.
        #
        # The archival is flushed on its own, before the new version is marked
        # published: `uq_workflow_versions_single_published` is a plain (not
        # deferrable) partial unique index, so if both statements land in one
        # flush the database sees two published rows for an instant and rejects
        # the write. Flush order within a single flush is not something to rely
        # on — this makes it explicit.
        previous = await self.versions.find_published(version.workflow_id)
        if previous is not None and previous.id != version.id:
            previous.status = WorkflowVersionStatus.ARCHIVED
            await self.session.flush()

        version.status = WorkflowVersionStatus.PUBLISHED
        version.published_at = dt.datetime.now(dt.UTC)
        async with integrity_guard(
            self.session, duplicate_message="У workflow уже есть опубликованная версия"
        ):
            await self.session.flush()
            await self.session.commit()
        return await self.versions.reload(version)

    def _require_draft(self, version: WorkflowVersion) -> None:
        if version.status != WorkflowVersionStatus.DRAFT:
            raise WorkflowVersionLockedError(
                "Структуру можно менять только в черновике. Создайте новую версию "
                "копированием опубликованной.",
                details={"version_id": str(version.id), "status": version.status.value},
            )

    # -- stages -------------------------------------------------------------

    async def list_stages(self, version_id: uuid.UUID) -> builtins.list[WorkflowStage]:
        await self.versions.get_or_fail(version_id)
        return await self.stages.list_for_version(version_id)

    async def add_stage(
        self,
        version_id: uuid.UUID,
        *,
        name: str,
        code: str | None = None,
        description: str | None = None,
        order_index: int | None = None,
        is_initial: bool = False,
        is_terminal: bool = False,
        is_final_success: bool = False,
    ) -> WorkflowStage:
        version = await self.versions.get_or_fail(version_id)
        self._require_draft(version)

        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название этапа не может быть пустым")

        if is_initial:
            current_initial = await self.stages.find_initial(version_id)
            if current_initial is not None:
                current_initial.is_initial = False
                await self.session.flush()

        stage = WorkflowStage(
            workflow_version_id=version_id,
            code=clean_text(code),
            name=cleaned,
            description=clean_text(description),
            order_index=(
                order_index
                if order_index is not None
                else await self.stages.max_order_index(version_id) + 10
            ),
            is_initial=is_initial,
            is_terminal=is_terminal,
            is_final_success=is_final_success,
        )
        self.stages.add(stage)
        async with integrity_guard(
            self.session, duplicate_message="Этап с таким кодом уже есть в этой версии"
        ):
            await self.session.flush()
            await self.session.commit()
        return stage

    async def rename_stage(self, stage_id: uuid.UUID, patch: dict[str, Any]) -> WorkflowStage:
        """Edit a stage's labels — allowed on a published version too (FR-03).

        Only descriptive fields are accepted here. Structural flags live in
        `update_stage_structure`, which requires a draft.
        """
        stage = await self.stages.get_or_fail(stage_id)
        allowed = {"name", "description"}
        unknown = set(patch) - allowed
        if unknown:
            raise WorkflowVersionLockedError(
                "Через переименование можно менять только название и описание этапа: "
                + ", ".join(sorted(unknown)),
                details={"fields": sorted(unknown)},
            )
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название этапа не может быть пустым")
            patch["name"] = name
        if "description" in patch:
            patch["description"] = clean_text(patch["description"])

        apply_patch(stage, patch)
        await self.session.flush()
        await self.session.commit()
        return stage

    async def update_stage_structure(
        self, stage_id: uuid.UUID, patch: dict[str, Any]
    ) -> WorkflowStage:
        stage = await self.stages.get_or_fail(stage_id)
        version = await self.versions.get_or_fail(stage.workflow_version_id)
        self._require_draft(version)

        if patch.get("is_initial"):
            current_initial = await self.stages.find_initial(version.id)
            if current_initial is not None and current_initial.id != stage.id:
                current_initial.is_initial = False
                await self.session.flush()

        apply_patch(stage, patch)
        async with integrity_guard(
            self.session, duplicate_message="Этап с таким кодом уже есть в этой версии"
        ):
            await self.session.flush()
            await self.session.commit()
        return stage

    async def delete_stage(self, stage_id: uuid.UUID) -> None:
        stage = await self.stages.get_or_fail(stage_id)
        version = await self.versions.get_or_fail(stage.workflow_version_id)
        self._require_draft(version)

        cards = await self.stages.count_cards_on_stage(stage_id)
        if cards:
            raise ValidationError(
                f"Нельзя удалить этап: на нём стоят карточки ({cards})",
                details={"blocked_by": {"карточки": cards}},
            )
        # Transitions touching the stage go with it; both live in the draft.
        for transition in await self.transitions.list_for_version(version.id):
            if stage_id in (transition.from_stage_id, transition.to_stage_id):
                await self.transitions.soft_delete(transition)
        await self.stages.soft_delete(stage)
        await self.session.commit()

    # -- transitions --------------------------------------------------------

    async def list_transitions(self, version_id: uuid.UUID) -> builtins.list[WorkflowTransition]:
        await self.versions.get_or_fail(version_id)
        return await self.transitions.list_for_version(version_id)

    async def add_transition(
        self,
        version_id: uuid.UUID,
        *,
        from_stage_id: uuid.UUID,
        to_stage_id: uuid.UUID,
        name: str | None = None,
        requires_comment: bool = True,
    ) -> WorkflowTransition:
        version = await self.versions.get_or_fail(version_id)
        self._require_draft(version)

        if from_stage_id == to_stage_id:
            raise ValidationError("Переход не может вести в тот же самый этап")

        stages = {stage.id: stage for stage in await self.stages.list_for_version(version_id)}
        for stage_id in (from_stage_id, to_stage_id):
            if stage_id not in stages:
                raise NotFoundError(
                    "Этап не найден в этой версии workflow",
                    details={"stage_id": str(stage_id)},
                )
        if await self.transitions.find_pair(version_id, from_stage_id, to_stage_id):
            raise DuplicateEntityError("Такой переход уже описан в этой версии")

        transition = WorkflowTransition(
            workflow_version_id=version_id,
            from_stage_id=from_stage_id,
            to_stage_id=to_stage_id,
            name=clean_text(name),
            requires_comment=requires_comment,
        )
        self.transitions.add(transition)
        async with integrity_guard(
            self.session, duplicate_message="Такой переход уже описан в этой версии"
        ):
            await self.session.flush()
            await self.session.commit()
        return await self.transitions.reload(transition)

    async def delete_transition(self, transition_id: uuid.UUID) -> None:
        transition = await self.transitions.get_or_fail(transition_id)
        version = await self.versions.get_or_fail(transition.workflow_version_id)
        self._require_draft(version)
        await self.transitions.soft_delete(transition)
        await self.session.commit()

    # -- graph (A7) ---------------------------------------------------------

    async def build_graph(self, version_id: uuid.UUID) -> dict[str, Any]:
        """Everything the frontend needs to draw the route in one request.

        Stages, the moves between them, and how many live cards sit on each
        stage — so the picture is a state of play, not just a diagram.
        """
        version = await self.versions.get_or_fail(version_id)
        workflow = await self.repo.get_or_fail(version.workflow_id)
        stages = await self.stages.list_for_version(version_id)
        transitions = await self.transitions.list_for_version(version_id)
        counts = await self.stages.count_cards_per_stage(version_id)

        return {
            "workflow": workflow,
            "version": version,
            "stages": stages,
            "transitions": transitions,
            "cards_per_stage": counts,
        }

    async def build_published_graph(self, workflow_id: uuid.UUID) -> dict[str, Any]:
        await self.repo.get_or_fail(workflow_id)
        published = await self.versions.find_published(workflow_id)
        if published is None:
            raise NotFoundError(
                "У workflow нет опубликованной версии",
                details={"workflow_id": str(workflow_id)},
            )
        return await self.build_graph(published.id)
