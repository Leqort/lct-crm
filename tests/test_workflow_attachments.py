"""Stage attachments (FR-04, SPEC-02 A5)."""

from __future__ import annotations

import pytest

from app.core.errors import (
    AttachmentInvalidFormatError,
    AttachmentTooLargeError,
    ValidationError,
)
from app.models.enums import AttachmentFormat, AuditAction
from app.schemas.interaction import InteractionCreate
from app.schemas.university import UniversityCreate
from app.services.attachments import AttachmentService
from app.services.audit import search_audit_log
from app.services.catalogs import UniversityService
from app.services.file_types import detect_attachment_format
from app.services.interactions import InteractionService
from app.services.workflow import WorkflowService
from app.services.workflow_presets import ensure_base_workflow
from tests.conftest import auth
from tests.factories import (
    ATTACHMENT_FILENAMES,
    ATTACHMENT_SAMPLES,
    make_docx,
    make_xlsx_sample,
)


async def _card_on_stage(session, scope, name: str = "МГТУ"):
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    version = (await service.list_versions(workflow.id))[0]
    stages = await service.list_stages(version.id)

    university = await UniversityService(session, scope).create(UniversityCreate(name=name))
    interaction = await InteractionService(session, scope).create(
        InteractionCreate(university_id=university.id)
    )
    return interaction, stages, version


@pytest.mark.parametrize("file_format", sorted(ATTACHMENT_SAMPLES))
def test_every_required_format_is_recognised(file_format):
    """Все десять форматов из ТЗ определяются по сигнатуре файла."""
    detected, content_type = detect_attachment_format(
        ATTACHMENT_SAMPLES[file_format], ATTACHMENT_FILENAMES[file_format]
    )
    assert detected == AttachmentFormat(file_format)
    assert content_type


def test_unsupported_format_is_refused():
    with pytest.raises(AttachmentInvalidFormatError):
        detect_attachment_format("просто текст".encode(), "заметка.txt")


def test_extension_must_match_the_content():
    """Имя файла не решает, что это за файл."""
    with pytest.raises(AttachmentInvalidFormatError) as excinfo:
        detect_attachment_format(make_xlsx_sample(), "смета.docx")
    assert excinfo.value.details["detected_format"] == "xlsx"

    with pytest.raises(AttachmentInvalidFormatError):
        detect_attachment_format(make_docx(), "письмо.pdf")


def test_renamed_executable_is_refused():
    """Классическая подмена: .exe с расширением .pdf."""
    with pytest.raises(AttachmentInvalidFormatError):
        detect_attachment_format(b"MZ\x90\x00" + b"\x00" * 64, "договор.pdf")


@pytest.mark.parametrize("file_format", sorted(ATTACHMENT_SAMPLES))
async def test_upload_accepts_every_required_format(session, scope, file_format):
    interaction, stages, _ = await _card_on_stage(session, scope)
    attachment = await AttachmentService(session, scope).upload(
        interaction.id,
        stages[0].id,
        filename=ATTACHMENT_FILENAMES[file_format],
        content=ATTACHMENT_SAMPLES[file_format],
        comment="приложено тестом",
    )
    assert attachment.file_format == AttachmentFormat(file_format)
    assert attachment.size_bytes == len(ATTACHMENT_SAMPLES[file_format])
    assert attachment.stage_id == stages[0].id


async def test_download_returns_the_same_bytes_and_is_audited(session, scope):
    interaction, stages, _ = await _card_on_stage(session, scope)
    service = AttachmentService(session, scope)
    original = ATTACHMENT_SAMPLES["pdf"]
    attachment = await service.upload(
        interaction.id, stages[0].id, filename="договор.pdf", content=original
    )

    fetched, data = await service.download(attachment.id)
    assert data == original
    assert fetched.file_hash == attachment.file_hash

    _, exports = await search_audit_log(
        session, entity_type="workflow_attachments", action=AuditAction.EXPORT
    )
    assert exports == 1


async def test_upload_is_audited_as_a_create(session, scope):
    interaction, stages, _ = await _card_on_stage(session, scope)
    await AttachmentService(session, scope).upload(
        interaction.id, stages[0].id, filename="схема.png", content=ATTACHMENT_SAMPLES["png"]
    )
    _, total = await search_audit_log(
        session, entity_type="workflow_attachments", action=AuditAction.CREATE
    )
    assert total == 1


async def test_oversized_file_is_refused(session, scope, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "attachment_max_file_size", 16)
    interaction, stages, _ = await _card_on_stage(session, scope)
    with pytest.raises(AttachmentTooLargeError):
        await AttachmentService(session, scope).upload(
            interaction.id, stages[0].id, filename="большой.pdf", content=ATTACHMENT_SAMPLES["pdf"]
        )


async def test_stage_from_another_version_is_refused(session, scope):
    """Файл нельзя повесить на статус, до которого карточка не может дойти."""
    interaction, _, _ = await _card_on_stage(session, scope)
    service = WorkflowService(session, scope)
    other = await service.create(name="Другой процесс")
    draft = await service.create_draft(other.id)
    foreign_stage = await service.add_stage(draft.id, name="Чужой этап", is_initial=True)

    with pytest.raises(ValidationError):
        await AttachmentService(session, scope).upload(
            interaction.id,
            foreign_stage.id,
            filename="схема.png",
            content=ATTACHMENT_SAMPLES["png"],
        )


async def test_soft_deleted_attachment_disappears_but_bytes_remain(session, scope):
    import sqlalchemy as sa

    interaction, stages, _ = await _card_on_stage(session, scope)
    service = AttachmentService(session, scope)
    attachment = await service.upload(
        interaction.id, stages[0].id, filename="схема.png", content=ATTACHMENT_SAMPLES["png"]
    )
    await service.delete(attachment.id)

    _, total = await service.list_for_interaction(interaction.id)
    assert total == 0

    blob = await session.scalar(
        sa.text("SELECT count(*) FROM workflow_attachment_blobs WHERE attachment_id = :id"),
        {"id": attachment.id},
    )
    assert blob == 1, "содержимое остаётся — очистка это вопрос политики хранения"


async def test_attachment_api_roundtrip(session, client, manager_user, scope):
    interaction, stages, _ = await _card_on_stage(session, scope)

    uploaded = await client.post(
        f"/api/v1/interactions/{interaction.id}/stages/{stages[0].id}/attachments",
        files={"file": ("договор.pdf", ATTACHMENT_SAMPLES["pdf"], "application/pdf")},
        data={"comment": "скан договора"},
        headers=auth(manager_user),
    )
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["file_format"] == "pdf"
    assert body["comment"] == "скан договора"

    listed = await client.get(
        f"/api/v1/interactions/{interaction.id}/attachments", headers=auth(manager_user)
    )
    assert listed.json()["total"] == 1

    downloaded = await client.get(
        f"/api/v1/attachments/{body['id']}/download", headers=auth(manager_user)
    )
    assert downloaded.status_code == 200
    assert downloaded.content == ATTACHMENT_SAMPLES["pdf"]
    assert downloaded.headers["content-type"] == "application/pdf"


async def test_attachment_api_rejects_mismatched_extension(session, client, manager_user, scope):
    interaction, stages, _ = await _card_on_stage(session, scope)
    response = await client.post(
        f"/api/v1/interactions/{interaction.id}/stages/{stages[0].id}/attachments",
        files={"file": ("отчёт.pdf", ATTACHMENT_SAMPLES["xlsx"], "application/pdf")},
        headers=auth(manager_user),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ATTACHMENT_INVALID_FORMAT"
