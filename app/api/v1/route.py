"""Run-time workflow API: moving a card along its route, and stage attachments."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from app.api.v1.deps import (
    READ_ERRORS,
    PaginationDep,
    ScopeDep,
    SessionDep,
    SortQuery,
    error_responses,
)
from app.core.errors import ErrorCode
from app.core.security import CurrentUser, require_admin, require_manager
from app.schemas.common import Page
from app.schemas.interaction import InteractionRead
from app.schemas.workflow import (
    AttachmentRead,
    RouteStartRequest,
    RouteView,
    StageHistoryRead,
    StageRead,
    TransitionRead,
    TransitionRequest,
    VersionRead,
)
from app.services.attachments import AttachmentService
from app.services.route import RouteService

router = APIRouter(prefix="/interactions", tags=["Workflow: ведение карточки"])
attachments_router = APIRouter(prefix="/attachments", tags=["Workflow: вложения"])

TRANSITION_ERRORS = error_responses(
    403,
    404,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "404": ErrorCode.NOT_FOUND.value,
        "422": (
            f"{ErrorCode.WORKFLOW_INVALID_TRANSITION.value}, {ErrorCode.VALIDATION_ERROR.value}"
        ),
    },
)

UPLOAD_ERRORS = error_responses(
    403,
    404,
    413,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "404": ErrorCode.NOT_FOUND.value,
        "413": ErrorCode.ATTACHMENT_TOO_LARGE.value,
        "422": (f"{ErrorCode.ATTACHMENT_INVALID_FORMAT.value}, {ErrorCode.VALIDATION_ERROR.value}"),
    },
)


@router.get(
    "/{interaction_id}/route",
    response_model=RouteView,
    summary="Маршрут карточки",
    description=(
        "Всё для отрисовки пути взаимодействия одним запросом (`FR-03`): схема этапов "
        "и переходов, текущее положение карточки, доступные с него переходы и полная "
        "история перемещений с комментариями.\n\n"
        "Схема берётся из **версии, на которой карточка стартовала**, а не из текущей "
        "опубликованной: публикация новой версии не перерисовывает маршрут работы, "
        "которая уже идёт."
    ),
    responses=READ_ERRORS,
)
async def get_route(interaction_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> RouteView:
    view = await RouteService(session, scope).route_view(interaction_id)
    interaction = view["interaction"]
    return RouteView(
        interaction_id=interaction.id,
        workflow_version_id=interaction.workflow_version_id,
        current_stage_id=interaction.current_stage_id,
        version=VersionRead.model_validate(view["version"]) if view["version"] else None,
        stages=[StageRead.model_validate(item) for item in view["stages"]],
        transitions=[TransitionRead.model_validate(item) for item in view["transitions"]],
        available_transitions=[
            TransitionRead.model_validate(item) for item in view["available_transitions"]
        ],
        history=[StageHistoryRead.model_validate(item) for item in view["history"]],
    )


@router.post(
    "/{interaction_id}/route/start",
    response_model=InteractionRead,
    summary="Поставить карточку на workflow",
    description=(
        "Ставит карточку на начальный этап опубликованной версии. Если `workflow_id` "
        "не передан, берётся workflow по умолчанию.\n\n"
        "Новые карточки встают на маршрут автоматически при создании и при импорте — "
        "эта ручка нужна для карточек, заведённых до того, как маршрут был настроен."
    ),
    responses=error_responses(
        403,
        404,
        422,
        **{
            "403": ErrorCode.ACCESS_DENIED.value,
            "404": ErrorCode.NOT_FOUND.value,
            "422": ErrorCode.VALIDATION_ERROR.value,
        },
    ),
)
async def start_route(
    interaction_id: uuid.UUID,
    data: RouteStartRequest,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> InteractionRead:
    interaction = await RouteService(session, scope).start(
        interaction_id, workflow_id=data.workflow_id, comment=data.comment
    )
    return InteractionRead.model_validate(interaction)


@router.post(
    "/{interaction_id}/transitions",
    response_model=InteractionRead,
    summary="Перевести карточку на другой этап",
    description=(
        "Переход разрешён, только если он описан в версии workflow, по которой идёт "
        "карточка; иначе — `422 WORKFLOW_INVALID_TRANSITION` со списком допустимых "
        "этапов в `details.allowed_stage_ids`.\n\n"
        "Если переход помечен `requires_comment`, комментарий обязателен. "
        "Он сохраняется в истории перемещений и попадает в аудит."
    ),
    responses=TRANSITION_ERRORS,
)
async def make_transition(
    interaction_id: uuid.UUID,
    data: TransitionRequest,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> InteractionRead:
    interaction = await RouteService(session, scope).transition(
        interaction_id, to_stage_id=data.to_stage_id, comment=data.comment
    )
    return InteractionRead.model_validate(interaction)


@router.get(
    "/{interaction_id}/history",
    response_model=list[StageHistoryRead],
    summary="История перемещений карточки",
    description="Все переходы по этапам с комментариями и авторами, в хронологическом порядке.",
    responses=READ_ERRORS,
)
async def get_history(
    interaction_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> list[StageHistoryRead]:
    history = await RouteService(session, scope).history_for(interaction_id)
    return [StageHistoryRead.model_validate(item) for item in history]


# --- attachments -----------------------------------------------------------


@router.get(
    "/{interaction_id}/attachments",
    response_model=Page[AttachmentRead],
    summary="Файлы, приложенные к карточке",
    description="Метаданные вложений. Можно сузить до одного этапа параметром `stage_id`.",
    responses=READ_ERRORS,
)
async def list_attachments(
    interaction_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    stage_id: uuid.UUID | None = Query(default=None, description="Фильтр по этапу"),
    sort: SortQuery = None,
) -> Page[AttachmentRead]:
    items, total = await AttachmentService(session, scope).list_for_interaction(
        interaction_id, stage_id=stage_id, page=page.page, size=page.size, sort=sort
    )
    return Page.build(
        [AttachmentRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.post(
    "/{interaction_id}/stages/{stage_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Приложить файл к этапу",
    description=(
        "Допустимые форматы (`FR-04`): png, jpeg, pdf, zip, gzip, rar, doc, docx, xls, xlsx.\n\n"
        "Тип определяется по сигнатуре файла, а не по имени: расширение обязано совпасть "
        "с фактическим содержимым, иначе вернётся `422 ATTACHMENT_INVALID_FORMAT`. "
        "Этап должен принадлежать той же версии workflow, по которой идёт карточка."
    ),
    responses=UPLOAD_ERRORS,
)
async def upload_attachment(
    interaction_id: uuid.UUID,
    stage_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    file: Annotated[UploadFile, File(description="Файл одного из десяти допустимых форматов")],
    comment: Annotated[str | None, Form(description="Комментарий к файлу")] = None,
    _: CurrentUser = Depends(require_manager),
) -> AttachmentRead:
    content = await file.read()
    attachment = await AttachmentService(session, scope).upload(
        interaction_id,
        stage_id,
        filename=file.filename or "file",
        content=content,
        comment=comment,
    )
    return AttachmentRead.model_validate(attachment)


@attachments_router.get(
    "/{attachment_id}",
    response_model=AttachmentRead,
    summary="Метаданные вложения",
    description="Имя, формат, размер и хеш файла без самого содержимого.",
    responses=READ_ERRORS,
)
async def get_attachment(
    attachment_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> AttachmentRead:
    return AttachmentRead.model_validate(await AttachmentService(session, scope).get(attachment_id))


@attachments_router.get(
    "/{attachment_id}/download",
    summary="Скачать вложение",
    description="Отдаёт содержимое файла. Каждое скачивание фиксируется в аудите как `export`.",
    responses=READ_ERRORS,
    response_class=Response,
)
async def download_attachment(
    attachment_id: uuid.UUID, session: SessionDep, scope: ScopeDep
) -> Response:
    attachment, data = await AttachmentService(session, scope).download(attachment_id)
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (f"attachment; filename*=UTF-8''{quote(attachment.filename)}")
        },
    )


@attachments_router.delete(
    "/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Удалить вложение (мягкое удаление)",
    description=(
        "Только для роли `admin`. Метаданные помечаются удалёнными, содержимое остаётся "
        "в базе: физическая очистка — вопрос политики хранения, а не кнопки «удалить»."
    ),
    responses=READ_ERRORS,
)
async def delete_attachment(
    attachment_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_admin),
) -> None:
    await AttachmentService(session, scope).delete(attachment_id)
