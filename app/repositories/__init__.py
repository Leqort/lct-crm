"""Data access layer. No business rules live here."""

from app.repositories.base import BaseRepository, visible_university_ids
from app.repositories.import_job import (
    ImportJobRepository,
    ImportMappingPresetRepository,
    ImportRowRepository,
)
from app.repositories.interaction import InteractionRepository
from app.repositories.product import ITDirectionRepository, ITProductRepository, VendorRepository
from app.repositories.university import (
    UniversityAssignmentRepository,
    UniversityContactRepository,
    UniversityRepository,
)
from app.repositories.user import UserRepository
from app.repositories.workflow import (
    InteractionStageHistoryRepository,
    WorkflowAttachmentRepository,
    WorkflowRepository,
    WorkflowStageRepository,
    WorkflowTransitionRepository,
    WorkflowVersionRepository,
)

__all__ = [
    "BaseRepository",
    "ITDirectionRepository",
    "ITProductRepository",
    "ImportJobRepository",
    "ImportMappingPresetRepository",
    "ImportRowRepository",
    "InteractionRepository",
    "InteractionStageHistoryRepository",
    "UniversityAssignmentRepository",
    "UniversityContactRepository",
    "UniversityRepository",
    "UserRepository",
    "VendorRepository",
    "WorkflowAttachmentRepository",
    "WorkflowRepository",
    "WorkflowStageRepository",
    "WorkflowTransitionRepository",
    "WorkflowVersionRepository",
    "visible_university_ids",
]
