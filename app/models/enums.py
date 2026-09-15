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


class WorkflowVersionStatus(StrEnum):
    """Lifecycle of one workflow version.

    Structural edits are only legal on a `draft`; publishing freezes the
    version so cards already running on it keep the route they started with.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AttachmentFormat(StrEnum):
    """The ten formats the customer requires for stage attachments (FR-04)."""

    PNG = "png"
    JPEG = "jpeg"
    PDF = "pdf"
    ZIP = "zip"
    GZIP = "gzip"
    RAR = "rar"
    DOC = "doc"
    DOCX = "docx"
    XLS = "xls"
    XLSX = "xlsx"


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
WORKFLOW_VERSION_STATUS_ENUM = "workflow_version_status"
ATTACHMENT_FORMAT_ENUM = "attachment_format"
