"""Interaction service and API: business key, upsert and derived expiry."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.errors import DuplicateEntityError
from app.schemas.interaction import InteractionCreate, InteractionUpdate
from app.schemas.product import ProductCreate, VendorCreate
from app.schemas.university import UniversityCreate
from app.services.catalogs import ITProductService, UniversityService, VendorService
from app.services.interactions import InteractionService, compute_license_expiry
from tests.conftest import auth


def test_expiry_is_derived_from_signature_and_term():
    assert compute_license_expiry(dt.date(2026, 2, 17), 3) == dt.date(2029, 2, 17)
    assert compute_license_expiry(None, 3) is None
    assert compute_license_expiry(dt.date(2026, 2, 17), None) is None
    # An explicit value always wins over the derived one.
    assert compute_license_expiry(dt.date(2026, 2, 17), 3, dt.date(2030, 1, 1)) == dt.date(
        2030, 1, 1
    )


def test_expiry_handles_leap_day():
    assert compute_license_expiry(dt.date(2028, 2, 29), 1) == dt.date(2029, 2, 28)


async def test_business_key_is_unique(session, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    service = InteractionService(session, scope)
    await service.create(InteractionCreate(university_id=university.id))
    with pytest.raises(DuplicateEntityError):
        await service.create(InteractionCreate(university_id=university.id))


async def test_upsert_updates_instead_of_duplicating(session, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    service = InteractionService(session, scope)

    first, created_first = await service.upsert(
        university_id=university.id,
        it_direction_id=None,
        it_product_id=None,
        values={
            "contract_number": "ДЛ-1",
            "license_signed_at": dt.date(2026, 1, 1),
            "license_years": 2,
        },
    )
    assert created_first is True
    assert first.license_expires_at == dt.date(2028, 1, 1)

    second, created_second = await service.upsert(
        university_id=university.id,
        it_direction_id=None,
        it_product_id=None,
        values={"contract_number": "ДЛ-2", "license_signed_at": None, "license_years": None},
    )
    assert created_second is False
    assert second.id == first.id
    assert second.contract_number == "ДЛ-2"
    # Empty incoming values never clear stored ones.
    assert second.license_signed_at == dt.date(2026, 1, 1)
    assert second.license_years == 2


async def test_update_recomputes_expiry(session, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    service = InteractionService(session, scope)
    interaction = await service.create(
        InteractionCreate(
            university_id=university.id,
            license_signed_at=dt.date(2026, 1, 1),
            license_years=2,
        )
    )
    updated = await service.update(interaction.id, InteractionUpdate(license_years=5))
    assert updated.license_expires_at == dt.date(2031, 1, 1)


async def test_filters_narrow_the_list(session, scope):
    universities = UniversityService(session, scope)
    first = await universities.create(UniversityCreate(name="МГТУ"))
    second = await universities.create(UniversityCreate(name="СПбПУ"))

    vendor = await VendorService(session, scope).create(VendorCreate(name="Астра"))
    product = await ITProductService(session, scope).create(
        ProductCreate(name="Astra Linux", vendor_id=vendor.id)
    )

    service = InteractionService(session, scope)
    await service.create(InteractionCreate(university_id=first.id, it_product_id=product.id))
    await service.create(InteractionCreate(university_id=second.id))

    _, by_university = await service.list(university_id=first.id)
    assert by_university == 1
    _, by_product = await service.list(it_product_id=product.id)
    assert by_product == 1
    _, all_rows = await service.list()
    assert all_rows == 2


async def test_period_filter_includes_overlapping_license(session, scope):
    universities = UniversityService(session, scope)
    first = await universities.create(UniversityCreate(name="Первый вуз"))
    second = await universities.create(UniversityCreate(name="Второй вуз"))
    service = InteractionService(session, scope)
    await service.create(
        InteractionCreate(
            university_id=first.id,
            license_signed_at=dt.date(2025, 1, 1),
            license_expires_at=dt.date(2027, 1, 1),
        )
    )
    await service.create(
        InteractionCreate(
            university_id=second.id,
            license_signed_at=dt.date(2023, 1, 1),
            license_expires_at=dt.date(2024, 1, 1),
        )
    )

    _, total = await service.list(period_from=dt.date(2026, 1, 1), period_to=dt.date(2026, 12, 31))
    assert total == 1


async def test_soft_deleted_interaction_disappears(session, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    service = InteractionService(session, scope)
    interaction = await service.create(InteractionCreate(university_id=university.id))
    await service.delete(interaction.id)

    _, total = await service.list()
    assert total == 0
    # The business key is free again.
    await service.create(InteractionCreate(university_id=university.id))


async def test_api_crud(session, client, manager_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))

    created = await client.post(
        "/api/v1/interactions",
        json={
            "university_id": str(university.id),
            "contract_number": "ДЛ-100",
            "license_signed_at": "2026-02-17",
            "license_years": 3,
        },
        headers=auth(manager_user),
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["license_expires_at"] == "2029-02-17"
    assert payload["university"]["name"] == "МГТУ"
    assert payload["workflow_version_id"] is None

    patched = await client.patch(
        f"/api/v1/interactions/{payload['id']}",
        json={"transfer_status": "Передано"},
        headers=auth(manager_user),
    )
    assert patched.json()["transfer_status"] == "Передано"

    duplicate = await client.post(
        "/api/v1/interactions",
        json={"university_id": str(university.id)},
        headers=auth(manager_user),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_ENTITY"


async def test_api_rejects_out_of_range_license_years(session, client, manager_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    response = await client.post(
        "/api/v1/interactions",
        json={"university_id": str(university.id), "license_years": 42},
        headers=auth(manager_user),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_api_create_loads_related_objects(session, client, manager_user, scope):
    """Regression: a just-created card must serialise its relations.

    A new ORM object has no relationships loaded, and the request session does
    not have the university in its identity map, so serialising the response
    used to raise `MissingGreenlet` — the row was written and the client still
    got a 500.
    """
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    vendor = await VendorService(session, scope).create(VendorCreate(name="Астра"))
    product = await ITProductService(session, scope).create(
        ProductCreate(name="Astra Linux", vendor_id=vendor.id)
    )

    response = await client.post(
        "/api/v1/interactions",
        json={"university_id": str(university.id), "it_product_id": str(product.id)},
        headers=auth(manager_user),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["university"]["name"] == "МГТУ"
    assert body["it_product"]["name"] == "Astra Linux"
    assert body["it_product"]["vendor"]["name"] == "Астра"


async def test_patch_of_unrelated_field_keeps_manual_expiry(session, scope):
    """Regression: editing the comment must not recompute a manual expiry date."""
    university = await UniversityService(session, scope).create(
        UniversityCreate(name="Вуз с ручной датой")
    )
    service = InteractionService(session, scope)
    interaction = await service.create(
        InteractionCreate(
            university_id=university.id,
            license_signed_at=dt.date(2026, 1, 1),
            license_years=3,
            license_expires_at=dt.date(2030, 12, 31),
        )
    )
    assert interaction.license_expires_at == dt.date(2030, 12, 31)

    updated = await service.update(interaction.id, InteractionUpdate(comment="просто правка"))
    assert updated.license_expires_at == dt.date(2030, 12, 31)

    # Changing an input of the formula does recompute it.
    recomputed = await service.update(interaction.id, InteractionUpdate(license_years=5))
    assert recomputed.license_expires_at == dt.date(2031, 1, 1)


async def test_upsert_does_not_clear_manual_expiry(session, scope):
    """Regression: an import row with no dates must not erase a stored expiry."""
    university = await UniversityService(session, scope).create(
        UniversityCreate(name="Вуз с ручной датой")
    )
    service = InteractionService(session, scope)
    await service.create(
        InteractionCreate(university_id=university.id, license_expires_at=dt.date(2030, 12, 31))
    )

    upserted, created = await service.upsert(
        university_id=university.id,
        it_direction_id=None,
        it_product_id=None,
        values={"contract_number": "ДЛ-5"},
    )
    assert created is False
    assert upserted.contract_number == "ДЛ-5"
    assert upserted.license_expires_at == dt.date(2030, 12, 31)
