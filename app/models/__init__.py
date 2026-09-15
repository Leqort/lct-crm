"""SQLAlchemy models. Importing this package registers every table on `Base`."""

from app.models.audit_log import AuditLog
from app.models.base import Base, DomainBase
from app.models.enums import (
    AttachmentFormat,
    AuditAction,
    ImportJobStatus,
    ImportRowStatus,
    ImportTarget,
    UserRole,
    WorkflowVersionStatus,
)
from app.models.import_job import ImportJob, ImportMappingPreset, ImportRow
from app.models.interaction import Interaction
from app.models.product import ITDirection, ITProduct, ITProductDirection, Vendor
from app.models.university import University, UniversityAssignment, UniversityContact
from app.models.user import User
from app.models.workflow import (
    InteractionStageHistory,
    Workflow,
    WorkflowAttachment,
    WorkflowAttachmentBlob,
    WorkflowStage,
    WorkflowTransition,
    WorkflowVersion,
)

__all__ = [
    "AttachmentFormat",
    "AuditAction",
    "AuditLog",
    "Base",
    "DomainBase",
    "ITDirection",
    "ITProduct",
    "ITProductDirection",
    "ImportJob",
    "ImportJobStatus",
    "ImportMappingPreset",
    "ImportRow",
    "ImportRowStatus",
    "ImportTarget",
    "Interaction",
    "InteractionStageHistory",
    "University",
    "UniversityAssignment",
    "UniversityContact",
    "User",
    "UserRole",
    "Vendor",
    "Workflow",
    "WorkflowAttachment",
    "WorkflowAttachmentBlob",
    "WorkflowStage",
    "WorkflowTransition",
    "WorkflowVersion",
    "WorkflowVersionStatus",
]
