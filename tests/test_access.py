"""Row-level visibility (SPEC §8) — enforced by the repository, not the router."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.access import AccessScope
from app.core.errors import AccessDeniedError
from app.models.enums import AuditAction
from app.schemas.interaction import InteractionCreate
from app.schemas.university import AssignmentCreate, UniversityCreate
from app.services.assignments import AssignmentService
from app.services.audit import search_audit_log
from app.services.catalogs import UniversityService
from app.services.interactions import InteractionService
from tests.conftest import auth


async def _university_with_kam(session, kam_user, name: str):
    universities = UniversityService(session, AccessScope.system())
    university = await universities.create(UniversityCreate(name=name))
    await AssignmentService(session, AccessScope.system()).create(
        university.id,
        AssignmentCreate(user_id=kam_user.id, assigned_from=dt.date.today() - dt.timedelta(days=1)),
    )
    return university


async def test_user_sees_only_assigned_universities(session, kam_user):
    mine = await _university_with_kam(session, kam_user, "Мой вуз")
    system = UniversityService(session, AccessScope.system())
    await system.create(UniversityCreate(name="Чужой вуз"))

    scoped = UniversityService(session, AccessScope(user_id=kam_user.id, is_privileged=False))
    items, total = await scoped.list()
    assert total == 1
    assert [item.id for item in items] == [mine.id]


async def test_user_gets_403_on_foreign_university_by_id(session, kam_user):
    await _university_with_kam(session, kam_user, "Мой вуз")
    system = UniversityService(session, AccessScope.system())
    foreign = await system.create(UniversityCreate(name="Чужой вуз"))

    scoped = UniversityService(session, AccessScope(user_id=kam_user.id, is_privileged=False))
    with pytest.raises(AccessDeniedError):
        await scoped.get(foreign.id)

    # The refusal itself is auditable (SPEC §8), and survives the rollback the
    # failing request performs.
    entries, total = await search_audit_log(session, action=AuditAction.ACCESS_DENIED)
    assert total == 1
    assert entries[0].entity_type == "universities"
    assert entries[0].entity_id == foreign.id


async def test_expired_assignment_removes_visibility(session, kam_user):
    system = UniversityService(session, AccessScope.system())
    university = await system.create(UniversityCreate(name="Бывший вуз"))
    assignments = AssignmentService(session, AccessScope.system())
    assignment = await assignments.create(
        university.id,
        AssignmentCreate(
            user_id=kam_user.id,
            assigned_from=dt.date.today() - dt.timedelta(days=30),
            assigned_to=dt.date.today() - dt.timedelta(days=1),
        ),
    )
    assert assignment.assigned_to is not None

    scoped = UniversityService(session, AccessScope(user_id=kam_user.id, is_privileged=False))
    _, total = await scoped.list()
    assert total == 0


async def test_interactions_inherit_university_scope(session, kam_user):
    mine = await _university_with_kam(session, kam_user, "Мой вуз")
    system_interactions = InteractionService(session, AccessScope.system())
    await system_interactions.create(InteractionCreate(university_id=mine.id))

    universities = UniversityService(session, AccessScope.system())
    foreign = await universities.create(UniversityCreate(name="Чужой вуз"))
    foreign_interaction = await system_interactions.create(
        InteractionCreate(university_id=foreign.id)
    )

    scoped = InteractionService(session, AccessScope(user_id=kam_user.id, is_privileged=False))
    _, total = await scoped.list()
    assert total == 1

    with pytest.raises(AccessDeniedError):
        await scoped.get(foreign_interaction.id)


async def test_manager_sees_everything(session, manager_user, kam_user):
    await _university_with_kam(session, kam_user, "Вуз КАМа")
    system = UniversityService(session, AccessScope.system())
    await system.create(UniversityCreate(name="Ничей вуз"))

    scoped = UniversityService(session, AccessScope(user_id=manager_user.id, is_privileged=True))
    _, total = await scoped.list()
    assert total == 2


# --- the same rules, over HTTP --------------------------------------------


async def test_api_user_cannot_read_foreign_university(session, client, kam_user):
    await _university_with_kam(session, kam_user, "Мой вуз")
    foreign = await UniversityService(session, AccessScope.system()).create(
        UniversityCreate(name="Чужой вуз")
    )

    listing = await client.get("/api/v1/universities", headers=auth(kam_user))
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    direct = await client.get(f"/api/v1/universities/{foreign.id}", headers=auth(kam_user))
    assert direct.status_code == 403
    assert direct.json()["error"]["code"] == "ACCESS_DENIED"


async def test_api_user_cannot_create_university(session, client, kam_user):
    response = await client.post(
        "/api/v1/universities", json={"name": "Самовольный вуз"}, headers=auth(kam_user)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


async def test_api_manager_cannot_delete(session, client, manager_user):
    university = await UniversityService(session, AccessScope.system()).create(
        UniversityCreate(name="Удаляемый вуз")
    )
    response = await client.delete(
        f"/api/v1/universities/{university.id}", headers=auth(manager_user)
    )
    assert response.status_code == 403


async def test_api_admin_can_delete(session, client, admin_user):
    university = await UniversityService(session, AccessScope.system()).create(
        UniversityCreate(name="Удаляемый вуз")
    )
    response = await client.delete(
        f"/api/v1/universities/{university.id}", headers=auth(admin_user)
    )
    assert response.status_code == 204


async def test_api_requires_identity(client):
    response = await client.get("/api/v1/universities")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


async def test_audit_endpoint_is_admin_only(session, client, manager_user, admin_user):
    denied = await client.get("/api/v1/audit", headers=auth(manager_user))
    assert denied.status_code == 403

    allowed = await client.get("/api/v1/audit", headers=auth(admin_user))
    assert allowed.status_code == 200
    assert "items" in allowed.json()
