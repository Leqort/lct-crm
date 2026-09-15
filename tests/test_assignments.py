"""KAM assignments: overlap rules and period closing."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.schemas.university import AssignmentCreate, UniversityCreate
from app.services.assignments import AssignmentService
from app.services.catalogs import UniversityService
from tests.conftest import auth


async def test_overlapping_assignment_is_rejected(session, scope, kam_user, other_kam_user):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    service = AssignmentService(session, scope)
    await service.create(
        university.id,
        AssignmentCreate(user_id=kam_user.id, assigned_from=dt.date(2026, 1, 1)),
    )
    with pytest.raises(ValidationError):
        await service.create(
            university.id,
            AssignmentCreate(user_id=other_kam_user.id, assigned_from=dt.date(2026, 6, 1)),
        )


async def test_sequential_assignments_are_allowed(session, scope, kam_user, other_kam_user):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    service = AssignmentService(session, scope)
    await service.create(
        university.id,
        AssignmentCreate(
            user_id=kam_user.id,
            assigned_from=dt.date(2026, 1, 1),
            assigned_to=dt.date(2026, 5, 31),
        ),
    )
    second = await service.create(
        university.id,
        AssignmentCreate(user_id=other_kam_user.id, assigned_from=dt.date(2026, 6, 1)),
    )
    assert second.assigned_to is None

    _, total = await service.list_for_university(university.id)
    assert total == 2


async def test_unknown_user_is_rejected(session, scope):
    import uuid

    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    with pytest.raises(NotFoundError):
        await AssignmentService(session, scope).create(
            university.id,
            AssignmentCreate(user_id=uuid.uuid4(), assigned_from=dt.date(2026, 1, 1)),
        )


async def test_close_sets_end_date_without_deleting(session, scope, kam_user):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    service = AssignmentService(session, scope)
    assignment = await service.create(
        university.id,
        AssignmentCreate(user_id=kam_user.id, assigned_from=dt.date(2026, 1, 1)),
    )
    closed = await service.close(assignment.id)
    assert closed.assigned_to == dt.date.today()
    assert closed.deleted_at is None

    _, total = await service.list_for_university(university.id)
    assert total == 1


async def test_close_is_idempotent(session, scope, kam_user):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    service = AssignmentService(session, scope)
    assignment = await service.create(
        university.id,
        AssignmentCreate(
            user_id=kam_user.id,
            assigned_from=dt.date(2026, 1, 1),
            assigned_to=dt.date(2026, 2, 1),
        ),
    )
    closed = await service.close(assignment.id)
    assert closed.assigned_to == dt.date(2026, 2, 1)


async def test_assignment_api_roundtrip(session, client, manager_user, kam_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))

    created = await client.post(
        f"/api/v1/universities/{university.id}/assignments",
        json={"user_id": str(kam_user.id), "assigned_from": "2026-01-01"},
        headers=auth(manager_user),
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["user"]["full_name"] == kam_user.full_name

    listed = await client.get(
        f"/api/v1/universities/{university.id}/assignments", headers=auth(manager_user)
    )
    assert listed.json()["total"] == 1

    closed = await client.delete(f"/api/v1/assignments/{payload['id']}", headers=auth(manager_user))
    assert closed.status_code == 200
    assert closed.json()["assigned_to"] == dt.date.today().isoformat()


async def test_assignment_api_reports_conflict(session, client, manager_user, kam_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="Вуз"))
    body = {"user_id": str(kam_user.id), "assigned_from": "2026-01-01"}
    first = await client.post(
        f"/api/v1/universities/{university.id}/assignments", json=body, headers=auth(manager_user)
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/universities/{university.id}/assignments", json=body, headers=auth(manager_user)
    )
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "VALIDATION_ERROR"
