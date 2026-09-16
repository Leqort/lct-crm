"""Design-time workflow API: templates, versions, stages, transitions, graph."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import (
    CATALOG_ERRORS,
    READ_ERRORS,
    PaginationDep,
    ScopeDep,
    SearchQuery,
    SessionDep,
    SortQuery,
    error_responses,
)
from app.core.errors import ErrorCode
from app.core.security import CurrentUser, require_admin, require_manager
from app.schemas.common import Page
from app.schemas.workflow import (
    StageCardCount,
    StageCreate,
    StageRead,
    StageRename,
    StageStructureUpdate,
    TransitionCreate,
    TransitionRead,
    VersionCreate,
    VersionRead,
    WorkflowCreate,
    WorkflowGraph,
    WorkflowRead,
    WorkflowUpdate,
)
from app.services.workflow import WorkflowService

router = APIRouter(prefix="/workflows", tags=["Workflow: настройка"])
stages_router = APIRouter(prefix="/workflow-stages", tags=["Workflow: настройка"])
transitions_router = APIRouter(prefix="/workflow-transitions", tags=["Workflow: настройка"])

STRUCTURE_ERRORS = error_responses(
    403,
    404,
    409,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "404": ErrorCode.NOT_FOUND.value,
        "409": f"{ErrorCode.WORKFLOW_VERSION_LOCKED.value}, {ErrorCode.DUPLICATE_ENTITY.value}",
        "422": ErrorCode.VALIDATION_ERROR.value,
    },
)


# --- templates -------------------------------------------------------------


@router.get(
    "",
    response_model=Page[WorkflowRead],
    summary="Список workflow",
    description="Маршруты работы с вузом. Базовый процесс из 14 шагов создаётся сид-скриптом.",
    responses=READ_ERRORS,
)
async def list_workflows(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[WorkflowRead]:
    items, total = await WorkflowService(session, scope).list(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [WorkflowRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowRead,
    summary="Карточка workflow",
    description="Возвращает шаблон маршрута по идентификатору.",
    responses=READ_ERRORS,
)
async def get_workflow(
    workflow_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> WorkflowRead:
    return WorkflowRead.model_validate(await WorkflowService(session, scope).get(workflow_id))


@router.post(
    "",
    response_model=WorkflowRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать workflow",
    description=(
        "Доступно ролям `manager` и `admin`. Создаётся пустой шаблон — этапы добавляются "
        "в черновик версии. Флаг `is_default` снимается с предыдущего workflow по умолчанию."
    ),
    responses=CATALOG_ERRORS,
)
async def create_workflow(
    data: WorkflowCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> WorkflowRead:
    workflow = await WorkflowService(session, scope).create(
        name=data.name, description=data.description, is_default=data.is_default
    )
    return WorkflowRead.model_validate(workflow)


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowRead,
    summary="Изменить workflow",
    description="Частичное обновление названия, описания и флагов шаблона.",
    responses=CATALOG_ERRORS,
)
async def update_workflow(
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> WorkflowRead:
    workflow = await WorkflowService(session, scope).update(
        workflow_id, data.model_dump(exclude_unset=True)
    )
    return WorkflowRead.model_validate(workflow)


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить workflow (мягкое удаление)",
    description=(
        "Только для роли `admin`. Запрещено, пока по опубликованной версии идут карточки: "
        "их сначала нужно перевести на другой маршрут."
    ),
    responses=CATALOG_ERRORS,
)
async def delete_workflow(
    workflow_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await WorkflowService(session, scope).delete(workflow_id)


# --- versions --------------------------------------------------------------


@router.get(
    "/{workflow_id}/versions",
    response_model=list[VersionRead],
    summary="Версии workflow",
    description=(
        "История версий маршрута. Опубликована всегда не более одной: именно на неё "
        "встают новые карточки. Предыдущая переводится в `archived`, но не удаляется — "
        "на неё ссылаются карточки, которые по ней идут."
    ),
    responses=READ_ERRORS,
)
async def list_versions(
    workflow_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> list[VersionRead]:
    versions = await WorkflowService(session, scope).list_versions(workflow_id)
    return [VersionRead.model_validate(item) for item in versions]


@router.post(
    "/{workflow_id}/versions",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать черновик версии",
    description=(
        "Структуру маршрута можно менять только в черновике. Чтобы изменить "
        "опубликованный маршрут, укажите `clone_from_id` — этапы и переходы скопируются "
        "с новыми идентификаторами, и правка не затронет карточки, идущие по оригиналу."
    ),
    responses=CATALOG_ERRORS,
)
async def create_version(
    workflow_id: uuid.UUID,
    data: VersionCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> VersionRead:
    version = await WorkflowService(session, scope).create_draft(
        workflow_id, clone_from_id=data.clone_from_id
    )
    return VersionRead.model_validate(version)


@router.post(
    "/{workflow_id}/versions/{version_id}/publish",
    response_model=VersionRead,
    summary="Опубликовать версию",
    description=(
        "Замораживает черновик и делает его маршрутом для новых карточек. "
        "Требует хотя бы один этап и заданный начальный этап. "
        "Предыдущая опубликованная версия уходит в архив."
    ),
    responses=STRUCTURE_ERRORS,
)
async def publish_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> VersionRead:
    return VersionRead.model_validate(await WorkflowService(session, scope).publish(version_id))


@router.get(
    "/{workflow_id}/versions/{version_id}/graph",
    response_model=WorkflowGraph,
    summary="Схема маршрута конкретной версии",
    description="Этапы, переходы и число живых карточек на каждом этапе — одним ответом.",
    responses=READ_ERRORS,
)
async def get_version_graph(
    workflow_id: uuid.UUID, version_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> WorkflowGraph:
    graph = await WorkflowService(session, scope).build_graph(version_id)
    return _graph_response(graph)


@router.get(
    "/{workflow_id}/graph",
    response_model=WorkflowGraph,
    summary="Схема опубликованного маршрута",
    description=(
        "То же, что схема версии, но без необходимости знать её идентификатор — "
        "берётся опубликованная версия workflow."
    ),
    responses=READ_ERRORS,
)
async def get_published_graph(
    workflow_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> WorkflowGraph:
    graph = await WorkflowService(session, scope).build_published_graph(workflow_id)
    return _graph_response(graph)


def _graph_response(graph: dict) -> WorkflowGraph:
    return WorkflowGraph(
        workflow=WorkflowRead.model_validate(graph["workflow"]),
        version=VersionRead.model_validate(graph["version"]),
        stages=[StageRead.model_validate(item) for item in graph["stages"]],
        transitions=[TransitionRead.model_validate(item) for item in graph["transitions"]],
        cards_per_stage=[
            StageCardCount(stage_id=stage_id, cards=count)
            for stage_id, count in graph["cards_per_stage"].items()
        ],
    )


# --- stages ----------------------------------------------------------------


@router.get(
    "/{workflow_id}/versions/{version_id}/stages",
    response_model=list[StageRead],
    summary="Этапы версии",
    description="Этапы маршрута в порядке отображения.",
    responses=READ_ERRORS,
)
async def list_stages(
    workflow_id: uuid.UUID, version_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> list[StageRead]:
    stages = await WorkflowService(session, scope).list_stages(version_id)
    return [StageRead.model_validate(item) for item in stages]


@router.post(
    "/{workflow_id}/versions/{version_id}/stages",
    response_model=StageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить этап",
    description=(
        "Только в черновике версии — на опубликованной вернётся `409 "
        "WORKFLOW_VERSION_LOCKED`. Установка `is_initial` снимает флаг с прежнего "
        "начального этапа."
    ),
    responses=STRUCTURE_ERRORS,
)
async def create_stage(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    data: StageCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> StageRead:
    stage = await WorkflowService(session, scope).add_stage(
        version_id,
        name=data.name,
        code=data.code,
        description=data.description,
        order_index=data.order_index,
        is_initial=data.is_initial,
        is_terminal=data.is_terminal,
        is_final_success=data.is_final_success,
    )
    return StageRead.model_validate(stage)


@stages_router.patch(
    "/{stage_id}",
    response_model=StageRead,
    summary="Переименовать этап",
    description=(
        "Меняет название и описание этапа — **в том числе в опубликованной версии** "
        "(`FR-03`). Это безопасно: идентификатор этапа не меняется, поэтому карточки, "
        "стоящие на нём, не ломаются. Структурные поля правятся отдельной ручкой "
        "и только в черновике."
    ),
    responses=STRUCTURE_ERRORS,
)
async def rename_stage(
    stage_id: uuid.UUID,
    data: StageRename,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> StageRead:
    stage = await WorkflowService(session, scope).rename_stage(
        stage_id, data.model_dump(exclude_unset=True)
    )
    return StageRead.model_validate(stage)


@stages_router.patch(
    "/{stage_id}/structure",
    response_model=StageRead,
    summary="Изменить структурные свойства этапа",
    description=(
        "Код, порядок, признаки начального, конечного и успешного завершения. "
        "Только в черновике версии."
    ),
    responses=STRUCTURE_ERRORS,
)
async def update_stage_structure(
    stage_id: uuid.UUID,
    data: StageStructureUpdate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> StageRead:
    stage = await WorkflowService(session, scope).update_stage_structure(
        stage_id, data.model_dump(exclude_unset=True)
    )
    return StageRead.model_validate(stage)


@stages_router.delete(
    "/{stage_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить этап",
    description=(
        "Только в черновике и только если на этапе не стоит ни одной карточки. "
        "Переходы, ведущие в этап и из него, удаляются вместе с ним."
    ),
    responses=STRUCTURE_ERRORS,
)
async def delete_stage(
    stage_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> None:
    await WorkflowService(session, scope).delete_stage(stage_id)


# --- transitions -----------------------------------------------------------


@router.get(
    "/{workflow_id}/versions/{version_id}/transitions",
    response_model=list[TransitionRead],
    summary="Переходы версии",
    description="Допустимые перемещения между этапами маршрута.",
    responses=READ_ERRORS,
)
async def list_transitions(
    workflow_id: uuid.UUID, version_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> list[TransitionRead]:
    transitions = await WorkflowService(session, scope).list_transitions(version_id)
    return [TransitionRead.model_validate(item) for item in transitions]


@router.post(
    "/{workflow_id}/versions/{version_id}/transitions",
    response_model=TransitionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить переход",
    description=(
        "Только в черновике версии. Оба этапа должны принадлежать этой же версии. "
        "`requires_comment` включён по умолчанию — комментарий при смене статуса "
        "требует `FR-03`."
    ),
    responses=STRUCTURE_ERRORS,
)
async def create_transition(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    data: TransitionCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> TransitionRead:
    transition = await WorkflowService(session, scope).add_transition(
        version_id,
        from_stage_id=data.from_stage_id,
        to_stage_id=data.to_stage_id,
        name=data.name,
        requires_comment=data.requires_comment,
    )
    return TransitionRead.model_validate(transition)


@transitions_router.delete(
    "/{transition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить переход",
    description="Только в черновике версии.",
    responses=STRUCTURE_ERRORS,
)
async def delete_transition(
    transition_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> None:
    await WorkflowService(session, scope).delete_transition(transition_id)
