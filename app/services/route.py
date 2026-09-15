"""Run-time workflow logic: putting a card on a route and moving it along.

A card stores the workflow **version** it started on, never the workflow. That
is what makes publishing a new version safe: an in-flight card keeps resolving
its stages and transitions against the frozen version it began with, even after
that version is archived (SPEC-02 A6).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import (
    NotFoundError,
    ValidationError,
    WorkflowInvalidTransitionError,
)
from app.models.interaction import Interaction
from app.models.workflow import (
    InteractionStageHistory,
    WorkflowStage,
    WorkflowTransition,
)
from app.repositories.interaction import InteractionRepository
from app.repositories.workflow import (
    InteractionStageHistoryRepository,
    WorkflowRepository,
    WorkflowStageRepository,
    WorkflowTransitionRepository,
    WorkflowVersionRepository,
)
from app.services.text import clean_text


class RouteService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.interactions = InteractionRepository(session, self.scope)
        self.workflows = WorkflowRepository(session, self.scope)
        self.versions = WorkflowVersionRepository(session, self.scope)
        self.stages = WorkflowStageRepository(session, self.scope)
        self.transitions = WorkflowTransitionRepository(session, self.scope)
        self.history = InteractionStageHistoryRepository(session, self.scope)

    # -- starting a card ----------------------------------------------------

    async def start(
        self,
        interaction_id: uuid.UUID,
        *,
        workflow_id: uuid.UUID | None = None,
        comment: str | None = None,
    ) -> Interaction:
        """Place a card on the first stage of a workflow's published version."""
        interaction = await self.interactions.get_or_fail(interaction_id)
        if interaction.current_stage_id is not None:
            raise ValidationError(
                "Карточка уже ведётся по workflow — используйте переход по этапам",
                details={"interaction_id": str(interaction_id)},
            )
        started = await self._place_on_initial_stage(
            interaction, workflow_id=workflow_id, comment=comment
        )
        if not started:
            raise NotFoundError(
                "Не найден workflow с опубликованной версией",
                details={"workflow_id": str(workflow_id) if workflow_id else None},
            )
        await self.session.commit()
        return await self.interactions.reload(interaction)

    async def start_if_configured(self, interaction: Interaction) -> bool:
        """Auto-start a freshly created card on the default workflow.

        Silent no-op when no default workflow is published yet — creating cards
        must keep working before anyone has configured a route, which is exactly
        the state the catalog import runs in on a fresh database.
        """
        if interaction.current_stage_id is not None:
            return False
        return await self._place_on_initial_stage(interaction, workflow_id=None, comment=None)

    async def _place_on_initial_stage(
        self,
        interaction: Interaction,
        *,
        workflow_id: uuid.UUID | None,
        comment: str | None,
    ) -> bool:
        if workflow_id is not None:
            workflow = await self.workflows.get_or_fail(workflow_id)
        else:
            default_workflow = await self.workflows.find_default()
            if default_workflow is None:
                return False
            workflow = default_workflow

        version = await self.versions.find_published(workflow.id)
        if version is None:
            return False
        initial = await self.stages.find_initial(version.id)
        if initial is None:
            return False

        interaction.workflow_version_id = version.id
        interaction.current_stage_id = initial.id
        self.history.add(
            InteractionStageHistory(
                interaction_id=interaction.id,
                university_id=interaction.university_id,
                workflow_version_id=version.id,
                from_stage_id=None,
                to_stage_id=initial.id,
                comment=clean_text(comment),
            )
        )
        await self.session.flush()
        return True

    # -- moving a card ------------------------------------------------------

    async def available_transitions(self, interaction: Interaction) -> list[WorkflowTransition]:
        if interaction.current_stage_id is None:
            return []
        return await self.transitions.list_from_stage(interaction.current_stage_id)

    async def transition(
        self,
        interaction_id: uuid.UUID,
        *,
        to_stage_id: uuid.UUID,
        comment: str | None = None,
    ) -> Interaction:
        """Move a card to another stage, recording why (FR-03)."""
        interaction = await self.interactions.get_or_fail(interaction_id)

        if interaction.current_stage_id is None or interaction.workflow_version_id is None:
            raise WorkflowInvalidTransitionError(
                "Карточка ещё не поставлена на workflow",
                details={"interaction_id": str(interaction_id)},
            )
        if to_stage_id == interaction.current_stage_id:
            raise WorkflowInvalidTransitionError(
                "Карточка уже находится на этом этапе",
                details={"stage_id": str(to_stage_id)},
            )

        # Resolved against the card's own version, not the current published
        # one: that is what keeps an in-flight route stable.
        transition = await self.transitions.find_pair(
            interaction.workflow_version_id, interaction.current_stage_id, to_stage_id
        )
        if transition is None:
            allowed = await self.transitions.list_from_stage(interaction.current_stage_id)
            raise WorkflowInvalidTransitionError(
                "Такой переход по этапам не разрешён в текущей версии workflow",
                details={
                    "from_stage_id": str(interaction.current_stage_id),
                    "to_stage_id": str(to_stage_id),
                    "allowed_stage_ids": [str(item.to_stage_id) for item in allowed],
                },
            )

        cleaned_comment = clean_text(comment)
        if transition.requires_comment and not cleaned_comment:
            raise ValidationError(
                "Для этого перехода обязателен комментарий",
                details={"field": "comment", "transition_id": str(transition.id)},
            )

        from_stage_id = interaction.current_stage_id
        interaction.current_stage_id = to_stage_id
        self.history.add(
            InteractionStageHistory(
                interaction_id=interaction.id,
                university_id=interaction.university_id,
                workflow_version_id=interaction.workflow_version_id,
                from_stage_id=from_stage_id,
                to_stage_id=to_stage_id,
                transition_id=transition.id,
                comment=cleaned_comment,
            )
        )
        await self.session.flush()
        await self.session.commit()
        return await self.interactions.reload(interaction)

    # -- reading the route (A7) ---------------------------------------------

    async def history_for(self, interaction_id: uuid.UUID) -> list[InteractionStageHistory]:
        await self.interactions.get_or_fail(interaction_id)
        return await self.history.list_for_interaction(interaction_id)

    async def route_view(self, interaction_id: uuid.UUID) -> dict[str, Any]:
        """One request with everything needed to render a card's path.

        The stages and transitions come from the card's own version, so the
        picture matches the route the card is actually running.
        """
        interaction = await self.interactions.get_or_fail(interaction_id)

        stages: list[WorkflowStage] = []
        transitions: list[WorkflowTransition] = []
        available: list[WorkflowTransition] = []
        version = None

        if interaction.workflow_version_id is not None:
            version = await self.versions.get_or_fail(interaction.workflow_version_id)
            stages = await self.stages.list_for_version(version.id)
            transitions = await self.transitions.list_for_version(version.id)
            available = await self.available_transitions(interaction)

        return {
            "interaction": interaction,
            "version": version,
            "stages": stages,
            "transitions": transitions,
            "available_transitions": available,
            "history": await self.history.list_for_interaction(interaction_id),
        }
