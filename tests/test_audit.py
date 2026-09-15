"""Automatic audit logging (SPEC §5, §11)."""

from __future__ import annotations

import sqlalchemy as sa

from app.core.access import AccessScope
from app.core.audit import AuditContext, audit_context, hash_pd
from app.models.enums import AuditAction
from app.schemas.university import ContactCreate, ContactUpdate, UniversityCreate, UniversityUpdate
from app.services.audit import search_audit_log
from app.services.catalogs import UniversityContactService, UniversityService
from tests.conftest import auth


async def test_create_is_audited(session, scope, admin_user):
    with audit_context(AuditContext(actor_id=admin_user.id, actor_name=admin_user.full_name)):
        university = await UniversityService(session, scope).create(
            UniversityCreate(name="Аудируемый вуз")
        )

    entries, total = await search_audit_log(
        session, entity_type="universities", entity_id=university.id
    )
    assert total == 1
    entry = entries[0]
    assert entry.action == AuditAction.CREATE
    assert entry.actor_id == admin_user.id
    assert entry.actor_name == admin_user.full_name
    assert entry.changes["name"]["new"] == "Аудируемый вуз"


async def test_update_records_only_changed_fields(session, scope):
    """SPEC §11: the diff must be exact, without created_at/updated_at noise."""
    service = UniversityService(session, scope)
    university = await service.create(UniversityCreate(name="Вуз", region="Москва"))
    await service.update(university.id, UniversityUpdate(region="Казань", comment="перевели"))

    entries, _ = await search_audit_log(
        session, entity_type="universities", entity_id=university.id, action=AuditAction.UPDATE
    )
    assert len(entries) == 1
    changes = entries[0].changes
    assert changes["region"] == {"old": "Москва", "new": "Казань"}
    assert changes["comment"] == {"old": None, "new": "перевели"}
    assert "name" not in changes
    assert "created_at" not in changes
    assert "updated_at" not in changes
    # Derived mirror columns are not interesting in a diff.
    assert "name_normalized" not in changes


async def test_unchanged_update_writes_nothing(session, scope):
    service = UniversityService(session, scope)
    university = await service.create(UniversityCreate(name="Вуз", region="Москва"))
    await service.update(university.id, UniversityUpdate(region="Москва"))

    _, total = await search_audit_log(
        session, entity_type="universities", entity_id=university.id, action=AuditAction.UPDATE
    )
    assert total == 0


async def test_personal_data_is_masked(session, scope):
    """SPEC §5.3: the log proves a change happened without storing plaintext."""
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    contacts = UniversityContactService(session, scope)
    contact = await contacts.create(
        university.id, ContactCreate(full_name="Иванов Иван", email="old@example.edu")
    )
    contact_id = contact.id
    await contacts.update(contact_id, ContactUpdate(email="new@example.edu"))

    entries, _ = await search_audit_log(
        session,
        entity_type="university_contacts",
        entity_id=contact_id,
        action=AuditAction.UPDATE,
    )
    changes = entries[0].changes
    assert changes["email"]["masked"] is True
    assert changes["email"]["old_sha256"] == hash_pd("old@example.edu")
    assert changes["email"]["new_sha256"] == hash_pd("new@example.edu")
    assert "old@example.edu" not in str(changes)
    assert "new@example.edu" not in str(changes)

    # The plaintext stays where it belongs — in the entity table.
    stored = await session.scalar(
        sa.text("SELECT email FROM university_contacts WHERE id = :id"), {"id": contact_id}
    )
    assert stored == "new@example.edu"


async def test_soft_delete_is_logged_as_delete(session, scope):
    service = UniversityService(session, scope)
    university = await service.create(UniversityCreate(name="Удаляемый вуз"))
    await service.delete(university.id)

    entries, total = await search_audit_log(
        session, entity_type="universities", entity_id=university.id, action=AuditAction.DELETE
    )
    assert total == 1
    assert entries[0].changes["deleted_at"]["old"] is None
    assert entries[0].changes["deleted_at"]["new"] is not None


async def test_audit_log_is_append_only(session, scope):
    """The database itself refuses to rewrite history."""
    await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    await session.commit()

    try:
        await session.execute(sa.text("UPDATE audit_log SET action = 'delete'"))
        await session.commit()
    except Exception as exc:  # we assert on the message
        assert "append-only" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("audit_log accepted an UPDATE")
    finally:
        await session.rollback()


async def test_reading_contacts_logs_read_pd(session, client, admin_user):
    university = await UniversityService(session, AccessScope.system()).create(
        UniversityCreate(name="Вуз с контактами")
    )
    await UniversityContactService(session, AccessScope.system()).create(
        university.id, ContactCreate(full_name="Петров Пётр")
    )

    response = await client.get(
        f"/api/v1/universities/{university.id}/contacts", headers=auth(admin_user)
    )
    assert response.status_code == 200

    _, total = await search_audit_log(session, action=AuditAction.READ_PD)
    assert total == 1


async def test_audit_api_filters(session, client, admin_user, scope):
    service = UniversityService(session, scope)
    university = await service.create(UniversityCreate(name="Фильтруемый вуз"))
    await session.commit()

    response = await client.get(
        "/api/v1/audit",
        params={"entity_type": "universities", "entity_id": str(university.id)},
        headers=auth(admin_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_type"] == "universities"
    assert body["items"][0]["action"] == "create"


async def test_request_id_travels_end_to_end(session, client, admin_user):
    response = await client.get("/api/v1/universities", headers=auth(admin_user))
    assert response.headers["X-Request-ID"]
