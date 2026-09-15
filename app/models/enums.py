"""Enumerations backed by native PostgreSQL enum types (SPEC §4.1)."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    MANAGER = "manager"
    ADMIN = "admin"


class ImportTarget(StrEnum):
    UNIVERSITIES = "universities"
    IT_PRODUCTS = "it_products"
    INTERACTIONS = "interactions"
    CONTACTS = "contacts"


class ImportJobStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportRowStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ_PD = "read_pd"
    IMPORT = "import"
    EXPORT = "export"
    LOGIN = "login"
    ACCESS_DENIED = "access_denied"


# Names of the PostgreSQL enum types. Kept in one place so models and Alembic
# migrations cannot drift apart.
USER_ROLE_ENUM = "user_role"
IMPORT_TARGET_ENUM = "import_target"
IMPORT_JOB_STATUS_ENUM = "import_job_status"
IMPORT_ROW_STATUS_ENUM = "import_row_status"
AUDIT_ACTION_ENUM = "audit_action"
