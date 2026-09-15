"""FastAPI application assembly: middleware, error contract, OpenAPI."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.audit import AuditContext, audit_context
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.errors import AppError, ErrorCode, error_payload
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = """
CRM ИТ Школы Ростелекома — ядро данных (SPEC-01).

**Что здесь есть:** справочники (вузы, вендоры, ИТ-направления, ИТ-продукты,
контакты вузов), карточки взаимодействий, двухфазный импорт каталогов из Excel
и журнал аудита.

**Чего здесь нет:** workflow-движок и переходы по этапам (SPEC-02), отчёты
(SPEC-03), интеграции с LMS и сайтом (SPEC-04). Модель данных под них
подготовлена.

### Авторизация
* `AUTH_MODE=dev` — заголовок `X-Debug-User` с UUID, `keycloak_id` или email
  существующего сотрудника. Запрещён при `ENV=production`.
* `AUTH_MODE=keycloak` — `Authorization: Bearer <JWT>`, проверка подписи по JWKS.

### Роли
* `user` — видит только закреплённые за собой вузы и связанные с ними данные;
* `manager` — видит всё, может создавать и изменять;
* `admin` — плюс мягкое удаление и доступ к журналу аудита.

### Формат ошибок
Любая ошибка возвращается в едином виде:

```json
{"error": {"code": "NOT_FOUND", "message": "Объект не найден",
           "details": {}, "request_id": "0d2f..."}}
```
"""

TAGS_METADATA = [
    {"name": "Справочник: вузы", "description": "Вузы, их контактные лица и закрепление КАМов."},
    {
        "name": "Справочник: контакты вузов",
        "description": "Персональные данные, чтение аудируется.",
    },
    {"name": "Справочник: назначения", "description": "Закрытие периодов закрепления КАМов."},
    {"name": "Справочник: вендоры", "description": "Вендоры программного обеспечения."},
    {"name": "Справочник: ИТ-направления", "description": "DevOps, QA, Data Science и т.д."},
    {"name": "Справочник: ИТ-продукты", "description": "ПО и лицензии, привязанные к вендорам."},
    {"name": "Взаимодействия", "description": "Карточки «вуз + направление + продукт»."},
    {"name": "Сотрудники", "description": "Локальная проекция пользователей Keycloak."},
    {"name": "Импорт каталогов", "description": "Загрузка → маппинг → предпросмотр → запись."},
    {"name": "Аудит", "description": "Журнал изменений, только для роли admin."},
    {"name": "Служебное", "description": "Проверки состояния сервиса."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "starting %s env=%s auth_mode=%s", settings.app_name, settings.env, settings.auth_mode
    )
    yield
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    """Establish the audit context for the whole request.

    The actor is filled in later by `get_current_user`; everything else (who
    called from where, under which request id) is known right now.
    """
    header_id = request.headers.get("X-Request-ID")
    try:
        request_id = uuid.UUID(header_id) if header_id else uuid.uuid4()
    except ValueError:
        request_id = uuid.uuid4()

    ctx = AuditContext(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=request_id,
    )
    request.state.request_id = request_id
    with audit_context(ctx):
        response = await call_next(request)
    response.headers["X-Request-ID"] = str(request_id)
    return response


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    if exc.http_status >= 500:
        logger.exception("unhandled domain error: %s", exc.message)
    return JSONResponse(
        status_code=exc.http_status,
        content=error_payload(
            exc.code, exc.message, details=exc.details, request_id=_request_id(request)
        ),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map FastAPI's own validation failures onto the single error contract."""
    return JSONResponse(
        status_code=422,
        content=error_payload(
            ErrorCode.VALIDATION_ERROR,
            "Переданные данные не прошли проверку",
            details={"fields": _safe_errors(exc.errors())},
            request_id=_request_id(request),
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        401: ErrorCode.ACCESS_DENIED,
        403: ErrorCode.ACCESS_DENIED,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.DUPLICATE_ENTITY,
        422: ErrorCode.VALIDATION_ERROR,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(code, str(exc.detail), request_id=_request_id(request)),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content=error_payload(
            ErrorCode.INTERNAL_ERROR,
            "Внутренняя ошибка сервера",
            request_id=_request_id(request),
        ),
    )


def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Strip `ctx`/`input` from validation errors: they may echo personal data."""
    return [
        {
            "loc": [str(part) for part in item.get("loc", [])],
            "msg": item.get("msg"),
            "type": item.get("type"),
        }
        for item in errors
    ]


@app.get(
    "/health",
    tags=["Служебное"],
    summary="Проверка живости сервиса",
    description='Возвращает `{"status": "ok"}`, если процесс обслуживает запросы.',
)
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.env}


app.include_router(api_router, prefix=settings.api_prefix)


def custom_openapi() -> dict[str, Any]:
    """Document the auth scheme that matches the configured `AUTH_MODE`."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=TAGS_METADATA,
    )
    if settings.auth_mode == "keycloak":
        scheme: dict[str, Any] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        name = "KeycloakJWT"
    else:
        scheme = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Debug-User",
            "description": "UUID, keycloak_id или email существующего сотрудника",
        }
        name = "DebugUser"

    schema.setdefault("components", {}).setdefault("securitySchemes", {})[name] = scheme
    schema["security"] = [{name: []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
