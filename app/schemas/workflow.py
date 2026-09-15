"""Contracts for the workflow engine (SPEC-02)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.models.enums import AttachmentFormat, WorkflowVersionStatus
from app.schemas.common import ORMModel

# --- templates and versions ------------------------------------------------


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300, examples=["Базовый процесс работы с вузом"])
    description: str | None = None
    is_default: bool = Field(
        default=False, description="Workflow, на который автоматически встают новые карточки"
    )


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class WorkflowRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class VersionCreate(BaseModel):
    clone_from_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Скопировать этапы и переходы из этой версии. Обычный способ изменить "
            "опубликованный маршрут: копия получает новые id этапов, поэтому правка "
            "не затрагивает карточки, которые идут по оригиналу"
        ),
    )


class VersionRead(ORMModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    version: int
    status: WorkflowVersionStatus
    published_at: dt.datetime | None
    comment: str | None
    created_at: dt.datetime


# --- stages ----------------------------------------------------------------


class StageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300, examples=["Подписание документов"])
    code: str | None = Field(default=None, max_length=50, examples=["WF-06"])
    description: str | None = None
    order_index: int | None = Field(
        default=None, description="Порядок отображения; если не задан — этап встаёт в конец"
    )
    is_initial: bool = False
    is_terminal: bool = False
    is_final_success: bool = False


class StageRename(BaseModel):
    """Editing labels only — разрешено и в опубликованной версии (FR-03)."""

    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None


class StageStructureUpdate(BaseModel):
    """Structural edits — только в черновике."""

    code: str | None = Field(default=None, max_length=50)
    order_index: int | None = None
    is_initial: bool | None = None
    is_terminal: bool | None = None
    is_final_success: bool | None = None


class StageRead(ORMModel):
    id: uuid.UUID
    workflow_version_id: uuid.UUID
    code: str | None
    name: str
    description: str | None
    order_index: int
    is_initial: bool
    is_terminal: bool
    is_final_success: bool


# --- transitions -----------------------------------------------------------


class TransitionCreate(BaseModel):
    from_stage_id: uuid.UUID
    to_stage_id: uuid.UUID
    name: str | None = Field(default=None, max_length=300, examples=["Документы подписаны"])
    requires_comment: bool = Field(
        default=True, description="Требовать комментарий при переходе (FR-03)"
    )


class TransitionRead(ORMModel):
    id: uuid.UUID
    workflow_version_id: uuid.UUID
    from_stage_id: uuid.UUID
    to_stage_id: uuid.UUID
    name: str | None
    requires_comment: bool


# --- graph (A7) ------------------------------------------------------------


class StageCardCount(BaseModel):
    stage_id: uuid.UUID
    cards: int


class WorkflowGraph(BaseModel):
    """Всё, что нужно для отрисовки схемы маршрута, одним ответом."""

    workflow: WorkflowRead
    version: VersionRead
    stages: list[StageRead]
    transitions: list[TransitionRead]
    cards_per_stage: list[StageCardCount] = Field(
        default_factory=list, description="Сколько живых карточек стоит на каждом этапе"
    )


# --- routing a card --------------------------------------------------------


class RouteStartRequest(BaseModel):
    workflow_id: uuid.UUID | None = Field(
        default=None, description="Если не задан — берётся workflow по умолчанию"
    )
    comment: str | None = None


class TransitionRequest(BaseModel):
    to_stage_id: uuid.UUID
    comment: str | None = Field(
        default=None,
        description="Обязателен, если переход помечен `requires_comment`",
        examples=["Договор подписан обеими сторонами"],
    )


class StageHistoryRead(ORMModel):
    id: uuid.UUID
    interaction_id: uuid.UUID
    workflow_version_id: uuid.UUID
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    transition_id: uuid.UUID | None
    comment: str | None
    created_at: dt.datetime
    created_by: uuid.UUID | None


class RouteView(BaseModel):
    """Маршрут конкретной карточки: схема, текущее положение и история."""

    interaction_id: uuid.UUID
    workflow_version_id: uuid.UUID | None
    current_stage_id: uuid.UUID | None
    version: VersionRead | None = None
    stages: list[StageRead] = Field(default_factory=list)
    transitions: list[TransitionRead] = Field(default_factory=list)
    available_transitions: list[TransitionRead] = Field(
        default_factory=list, description="Переходы, доступные с текущего этапа"
    )
    history: list[StageHistoryRead] = Field(default_factory=list)


# --- attachments -----------------------------------------------------------


class AttachmentRead(ORMModel):
    id: uuid.UUID
    interaction_id: uuid.UUID
    stage_id: uuid.UUID
    filename: str
    file_format: AttachmentFormat
    content_type: str
    size_bytes: int
    file_hash: str
    comment: str | None
    created_at: dt.datetime
    created_by: uuid.UUID | None
