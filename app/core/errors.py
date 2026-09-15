"""Domain exceptions and the single error-response contract (SPEC §10)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable machine-readable error codes — a direct requirement (NFT #3)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    IMPORT_INVALID_FORMAT = "IMPORT_INVALID_FORMAT"
    IMPORT_FILE_TOO_LARGE = "IMPORT_FILE_TOO_LARGE"
    IMPORT_MAPPING_INCOMPLETE = "IMPORT_MAPPING_INCOMPLETE"
    IMPORT_JOB_WRONG_STATE = "IMPORT_JOB_WRONG_STATE"
    # Added by SPEC-02: the frontend needs to tell "you cannot move there" apart
    # from "the payload is malformed", and both would otherwise be
    # VALIDATION_ERROR.
    WORKFLOW_INVALID_TRANSITION = "WORKFLOW_INVALID_TRANSITION"
    WORKFLOW_VERSION_LOCKED = "WORKFLOW_VERSION_LOCKED"
    ATTACHMENT_INVALID_FORMAT = "ATTACHMENT_INVALID_FORMAT"
    ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Base class for every error we deliberately return to the client.

    `message` is user-facing and therefore in Russian; `code` and `details`
    are for machines.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    message: str = "Внутренняя ошибка сервера"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 422
    message = "Переданные данные не прошли проверку"


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND
    http_status = 404
    message = "Объект не найден"


class AccessDeniedError(AppError):
    code = ErrorCode.ACCESS_DENIED
    http_status = 403
    message = "Доступ к объекту запрещён"


class DuplicateEntityError(AppError):
    code = ErrorCode.DUPLICATE_ENTITY
    http_status = 409
    message = "Объект с такими данными уже существует"


class ImportInvalidFormatError(AppError):
    code = ErrorCode.IMPORT_INVALID_FORMAT
    http_status = 422
    message = "Файл не является корректной таблицей Excel"


class ImportFileTooLargeError(AppError):
    code = ErrorCode.IMPORT_FILE_TOO_LARGE
    http_status = 413
    message = "Размер файла превышает допустимый предел"


class ImportMappingIncompleteError(AppError):
    code = ErrorCode.IMPORT_MAPPING_INCOMPLETE
    http_status = 422
    message = "Маппинг колонок заполнен не полностью"


class ImportJobWrongStateError(AppError):
    code = ErrorCode.IMPORT_JOB_WRONG_STATE
    http_status = 409
    message = "Операция недопустима в текущем состоянии задачи импорта"


class WorkflowInvalidTransitionError(AppError):
    code = ErrorCode.WORKFLOW_INVALID_TRANSITION
    http_status = 422
    message = "Такой переход по этапам не разрешён"


class WorkflowVersionLockedError(AppError):
    """Structural edits are only legal while a version is a draft (SPEC-02 A6)."""

    code = ErrorCode.WORKFLOW_VERSION_LOCKED
    http_status = 409
    message = "Версия workflow опубликована: структуру можно менять только в черновике"


class AttachmentInvalidFormatError(AppError):
    code = ErrorCode.ATTACHMENT_INVALID_FORMAT
    http_status = 422
    message = "Формат файла не поддерживается"


class AttachmentTooLargeError(AppError):
    code = ErrorCode.ATTACHMENT_TOO_LARGE
    http_status = 413
    message = "Размер файла превышает допустимый предел"


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    http_status = 500
    message = "Внутренняя ошибка сервера"


def error_payload(
    code: ErrorCode | str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the single response envelope described in SPEC §10."""
    return {
        "error": {
            "code": str(code),
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }
