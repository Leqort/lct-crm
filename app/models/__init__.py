"""SQLAlchemy models. Importing this package registers every table on `Base`."""

from app.models.audit_log import AuditLog
from app.models.base import Base, DomainBase
from app.models.enums import (
    AuditAction,
    ImportJobStatus,
    ImportRowStatus,
    ImportTarget,
    UserRole,
)
from app.models.import_job import ImportJob, ImportMappingPreset, ImportRow
from app.models.interaction import Interaction
from app.models.product import ITDirection, ITProduct, ITProductDirection, Vendor
from app.models.university import University, UniversityAssignment, UniversityContact
from app.models.user import User

__all__ = [
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
    "University",
    "UniversityAssignment",
    "UniversityContact",
    "User",
    "UserRole",
    "Vendor",
]
