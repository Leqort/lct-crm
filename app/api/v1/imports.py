"""XLSX import endpoints (SPEC §6, §7)."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

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
from app.core.errors import ErrorCode, ImportInvalidFormatError
from app.core.security import CurrentUser, require_manager
from app.imports.importer import ImportService
from app.imports.report import build_report
from app.models.enums import ImportRowStatus, ImportTarget
from app.schemas.common import Page
from app.schemas.import_job import (
    ImportCommitResult,
    ImportJobCreated,
    ImportJobRead,
    ImportRowRead,
    ImportStats,
    MappingRequest,
    MappingSuggestion,
    PresetCreate,
    PresetRead,
)
from app.services.audit import log_export

router = APIRouter(prefix="/imports", tags=["Импорт каталогов"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

UPLOAD_ERRORS = error_responses(
    403,
    413,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "413": ErrorCode.IMPORT_FILE_TOO_LARGE.value,
        "422": f"{ErrorCode.IMPORT_INVALID_FORMAT.value}, {ErrorCode.VALIDATION_ERROR.value}",
    },
)

JOB_STATE_ERRORS = error_responses(
    403,
    404,
    409,
    422,
    **{
        "403": ErrorCode.ACCESS_DENIED.value,
        "404": ErrorCode.NOT_FOUND.value,
        "409": ErrorCode.IMPORT_JOB_WRONG_STATE.value,
        "422": f"{ErrorCode.IMPORT_MAPPING_INCOMPLETE.value}, {ErrorCode.VALIDATION_ERROR.value}",
    },
)


@router.post(
    "",
    response_model=ImportJobCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Шаг 1. Загрузить файл каталога",
    description=(
        "Принимает `.xlsx` или `.xls`.\n\n"
        "Проверки: реальный формат определяется по сигнатуре файла (имя и "
        "`Content-Type` не используются), размер ≤ 20 МБ, в файле есть хотя бы "
        "одна непустая строка данных.\n\n"
        "В ответе — `job_id`, заголовки первой строки и автоматически "
        "предложенный маппинг колонок. Если файл с таким же содержимым уже "
        "загружался, приходит предупреждение, но загрузка не блокируется: "
        "повторный импорт идемпотентен благодаря upsert."
    ),
    responses=UPLOAD_ERRORS,
)
async def upload_import(
    session: SessionDep,
    scope: ScopeDep,
    file: Annotated[UploadFile, File(description="Файл каталога .xlsx или .xls")],
    target: Annotated[
        ImportTarget,
        Form(description="Что импортируем: взаимодействия, вузы, ПО или контакты"),
    ] = ImportTarget.INTERACTIONS,
    _: CurrentUser = Depends(require_manager),
) -> ImportJobCreated:
    content = await file.read()
    if not content:
        raise ImportInvalidFormatError("Файл пуст", details={"filename": file.filename or ""})

    result = await ImportService(session, scope).create_job(
        filename=file.filename or "catalog.xlsx", content=content, target=target
    )
    return ImportJobCreated(
        job=ImportJobRead.model_validate(result["job"]),
        headers=result["headers"],
        suggested_mapping=[MappingSuggestion(**item) for item in result["suggested_mapping"]],
        duplicate_of=result["duplicate_of"],
        warnings=result["warnings"],
    )


@router.get(
    "",
    response_model=Page[ImportJobRead],
    summary="История импортов",
    description="Список задач импорта с их статусами и статистикой.",
    responses=READ_ERRORS,
)
async def list_jobs(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[ImportJobRead]:
    items, total = await ImportService(session, scope).list_jobs(
        page=page.page, size=page.size, search=search, sort=sort
    )
    return Page.build(
        [ImportJobRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.get(
    "/presets",
    response_model=Page[PresetRead],
    summary="Сохранённые пресеты маппинга",
    description="Готовые схемы «колонка файла → поле системы», чтобы не размечать файл заново.",
    responses=READ_ERRORS,
)
async def list_presets(
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    target: ImportTarget | None = Query(default=None, description="Фильтр по типу импорта"),
) -> Page[PresetRead]:
    items, total = await ImportService(session, scope).list_presets(
        target=target, page=page.page, size=page.size
    )
    return Page.build(
        [PresetRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.post(
    "/presets",
    response_model=PresetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Сохранить пресет маппинга",
    description="Доступно ролям `manager` и `admin`. Имя пресета уникально в рамках типа импорта.",
    responses=CATALOG_ERRORS,
)
async def create_preset(
    data: PresetCreate,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> PresetRead:
    preset = await ImportService(session, scope).create_preset(
        name=data.name, target=data.target, mapping=data.mapping
    )
    return PresetRead.model_validate(preset)


@router.get(
    "/{job_id}",
    response_model=ImportJobRead,
    summary="Состояние задачи импорта",
    description="Текущий статус, маппинг и статистика задачи.",
    responses=READ_ERRORS,
)
async def get_job(job_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> ImportJobRead:
    return ImportJobRead.model_validate(await ImportService(session, scope).get_job(job_id))


@router.post(
    "/{job_id}/mapping",
    response_model=ImportJobRead,
    summary="Шаг 2. Подтвердить маппинг колонок",
    description=(
        "Пользователь подтверждает или правит соответствие «колонка файла → поле системы».\n\n"
        "Проверяется: все колонки существуют в файле, поля допустимы для выбранного типа "
        "импорта, одно поле не сопоставлено двум колонкам, обязательные поля заполнены "
        "(иначе `IMPORT_MAPPING_INCOMPLETE`). Смена маппинга сбрасывает результат "
        "предыдущей валидации."
    ),
    responses=JOB_STATE_ERRORS,
)
async def set_mapping(
    job_id: uuid.UUID,
    data: MappingRequest,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ImportJobRead:
    job = await ImportService(session, scope).set_mapping(
        job_id, data.mapping, save_as_preset=data.save_as_preset
    )
    return ImportJobRead.model_validate(job)


@router.post(
    "/{job_id}/validate",
    response_model=ImportCommitResult,
    summary="Шаг 3. Предпросмотр (dry-run)",
    description=(
        "Разбирает каждую строку и раскладывает её по полям, ничего не записывая.\n\n"
        "Правила: пустое «Название ВУЗа» → `error`, строка не импортируется; "
        "нераспознанная дата или срок лицензии вне диапазона 1–10 → `warning`, "
        "поле остаётся пустым; ненайденный менеджер → `warning`; похожий по названию "
        "вуз предлагается в сообщении, но автоматически **не** сливается; дубли внутри "
        "файла помечаются `warning`, применяется последняя строка."
    ),
    responses=JOB_STATE_ERRORS,
)
async def validate_job(
    job_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ImportCommitResult:
    job, stats = await ImportService(session, scope).validate(job_id)
    return ImportCommitResult(job=ImportJobRead.model_validate(job), stats=ImportStats(**stats))


@router.get(
    "/{job_id}/rows",
    response_model=Page[ImportRowRead],
    summary="Постраничный предпросмотр строк",
    description="Разобранные строки файла с их статусами и сообщениями валидатора.",
    responses=READ_ERRORS,
)
async def list_rows(
    job_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    page: PaginationDep,
    row_status: ImportRowStatus | None = Query(
        default=None, alias="status", description="Фильтр по статусу строки"
    ),
) -> Page[ImportRowRead]:
    items, total = await ImportService(session, scope).list_rows(
        job_id, status=row_status, page=page.page, size=page.size
    )
    return Page.build(
        [ImportRowRead.model_validate(item) for item in items], total, page.page, page.size
    )


@router.post(
    "/{job_id}/commit",
    response_model=ImportCommitResult,
    summary="Шаг 4. Записать данные",
    description=(
        "Выполняется целиком в одной транзакции: любая непредвиденная ошибка "
        "откатывает всё и переводит задачу в статус `failed`.\n\n"
        "Строки со статусом `error` пропускаются и не блокируют остальные. "
        "Запись выполняется через upsert по ключу «вуз + направление + продукт», "
        "поэтому повторный импорт того же файла не создаёт дублей. "
        "Пустая ячейка означает «нет данных» и **не** затирает заполненное значение в БД.\n\n"
        "В аудит-лог пишется сводная запись `import` плюс обычные `create`/`update` "
        "по всем затронутым сущностям."
    ),
    responses=JOB_STATE_ERRORS,
)
async def commit_job(
    job_id: uuid.UUID,
    session: SessionDep,
    scope: ScopeDep,
    _: CurrentUser = Depends(require_manager),
) -> ImportCommitResult:
    job, stats = await ImportService(session, scope).commit(job_id)
    return ImportCommitResult(job=ImportJobRead.model_validate(job), stats=ImportStats(**stats))


@router.get(
    "/{job_id}/report",
    summary="Шаг 5. Отчёт по импорту (xlsx)",
    description=(
        "Возвращает исходные строки файла с добавленными колонками «Результат импорта» "
        "и «Замечания», чтобы пользователь исправил файл и загрузил его повторно. "
        "Выгрузка фиксируется в аудит-логе действием `export`."
    ),
    responses=READ_ERRORS,
    response_class=Response,
)
async def download_report(job_id: uuid.UUID, session: SessionDep, scope: ScopeDep) -> Response:
    service = ImportService(session, scope)
    job = await service.get_job(job_id)
    rows = await service.all_rows(job_id)
    content = build_report(job, rows)

    await log_export(
        session, entity_type="import_jobs", entity_id=job.id, rows=len(rows), format="xlsx"
    )
    await session.commit()

    filename = f"import-report-{job.id}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )
