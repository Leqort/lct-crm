"""Stage attachments (FR-04, SPEC-02 A5).

A file belongs to one stage of one card. Both the upload and the download are
audited: the upload as an ordinary ORM `create`, the download explicitly as an
`export`, because reading a document out of the system is exactly the event an
auditor asks about.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.config import settings
from app.core.errors import (
    AttachmentTooLargeError,
    NotFoundError,
    ValidationError,
)
from app.models.workflow import WorkflowAttachment
from app.repositories.interaction import InteractionRepository
from app.repositories.workflow import WorkflowAttachmentRepository, WorkflowStageRepository
from app.services.audit import log_export
from app.services.file_types import detect_attachment_format
from app.services.text import clean_text


class AttachmentService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = WorkflowAttachmentRepository(session, self.scope)
        self.interactions = InteractionRepository(session, self.scope)
        self.stages = WorkflowStageRepository(session, self.scope)

    async def list_for_interaction(
        self,
        interaction_id: uuid.UUID,
        *,
        stage_id: uuid.UUID | None = None,
        page: int = 1,
        size: int = 50,
        sort: str | None = None,
    ) -> tuple[list[WorkflowAttachment], int]:
        # Access is decided on the card: asking for a foreign card's files is a
        # refusal, not an empty list.
        await self.interactions.get_or_fail(interaction_id)
        filters = [WorkflowAttachment.interaction_id == interaction_id]
        if stage_id is not None:
            filters.append(WorkflowAttachment.stage_id == stage_id)
        return await self.repo.list(page=page, size=size, sort=sort, extra_filters=filters)

    async def upload(
        self,
        interaction_id: uuid.UUID,
        stage_id: uuid.UUID,
        *,
        filename: str,
        content: bytes,
        comment: str | None = None,
    ) -> WorkflowAttachment:
        interaction = await self.interactions.get_or_fail(interaction_id)

        if len(content) > settings.attachment_max_file_size:
            raise AttachmentTooLargeError(
                "Размер файла превышает допустимый предел",
                details={
                    "filename": filename,
                    "size": len(content),
                    "max_size": settings.attachment_max_file_size,
                },
            )

        stage = await self.stages.get_or_fail(stage_id)
        # The stage must belong to the route this card is actually running,
        # otherwise the file would hang off a status the card can never reach.
        if interaction.workflow_version_id is None:
            raise ValidationError(
                "Карточка не поставлена на workflow — прикладывать файлы не к чему",
                details={"interaction_id": str(interaction_id)},
            )
        if stage.workflow_version_id != interaction.workflow_version_id:
            raise ValidationError(
                "Этап относится к другой версии workflow, чем карточка",
                details={"stage_id": str(stage_id)},
            )

        cleaned_name = clean_text(filename) or "file"
        file_format, content_type = detect_attachment_format(content, cleaned_name)

        attachment = WorkflowAttachment(
            interaction_id=interaction.id,
            university_id=interaction.university_id,
            stage_id=stage_id,
            filename=cleaned_name,
            file_format=file_format,
            content_type=content_type,
            size_bytes=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            comment=clean_text(comment),
        )
        self.repo.add(attachment)
        await self.session.flush()
        await self.repo.store_blob(attachment.id, content)
        await self.session.commit()
        return await self.repo.reload(attachment)

    async def get(self, attachment_id: uuid.UUID) -> WorkflowAttachment:
        return await self.repo.get_or_fail(attachment_id)

    async def download(self, attachment_id: uuid.UUID) -> tuple[WorkflowAttachment, bytes]:
        attachment = await self.repo.get_or_fail(attachment_id)
        data = await self.repo.read_blob(attachment_id)
        if data is None:
            raise NotFoundError(
                "Содержимое файла не найдено", details={"attachment_id": str(attachment_id)}
            )
        await log_export(
            self.session,
            entity_type="workflow_attachments",
            entity_id=attachment_id,
            filename=attachment.filename,
            size=attachment.size_bytes,
        )
        await self.session.commit()
        return attachment, data

    async def delete(self, attachment_id: uuid.UUID) -> None:
        """Soft delete: the metadata row stays, and so do the bytes.

        Purging blobs is a retention decision for the customer, not something
        a delete button should do silently.
        """
        attachment = await self.repo.get_or_fail(attachment_id)
        await self.repo.soft_delete(attachment)
        await self.session.commit()
