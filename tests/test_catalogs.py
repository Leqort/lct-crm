"""Catalog services: CRUD, normalisation, duplicates and soft delete."""

from __future__ import annotations

import pytest

from app.core.access import AccessScope
from app.core.errors import DuplicateEntityError, NotFoundError, ValidationError
from app.schemas.product import (
    DirectionCreate,
    DirectionUpdate,
    ProductCreate,
    ProductUpdate,
    VendorCreate,
    VendorUpdate,
)
from app.schemas.university import (
    ContactCreate,
    ContactUpdate,
    UniversityCreate,
    UniversityUpdate,
)
from app.services.catalogs import (
    ITDirectionService,
    ITProductService,
    UniversityContactService,
    UniversityService,
    VendorService,
)


async def test_create_and_get_university(session, scope):
    service = UniversityService(session, scope)
    created = await service.create(
        UniversityCreate(name="  МГТУ   им.  Баумана ", region="г. Москва")
    )
    # Whitespace is collapsed on the way in; the file's spelling is otherwise kept.
    assert created.name == "МГТУ им. Баумана"
    assert created.name_normalized == "мгту им баумана"

    fetched = await service.get(created.id)
    assert fetched.id == created.id


async def test_university_name_is_unique_among_live_rows(session, scope):
    service = UniversityService(session, scope)
    await service.create(UniversityCreate(name="КФУ"))
    with pytest.raises(DuplicateEntityError):
        await service.create(UniversityCreate(name="кфу"))


async def test_soft_delete_hides_row_but_keeps_it(session, scope):
    """SPEC §11: a deleted object disappears from listings but stays in the DB."""
    service = UniversityService(session, scope)
    university = await service.create(UniversityCreate(name="ЮФУ"))
    await service.delete(university.id)

    items, total = await service.list()
    assert total == 0
    assert items == []

    with pytest.raises(NotFoundError):
        await service.get(university.id)

    row = (
        await session.execute(
            __import__("sqlalchemy").text("SELECT deleted_at FROM universities WHERE id = :id"),
            {"id": university.id},
        )
    ).first()
    assert row is not None and row[0] is not None

    # The freed name can be used again.
    await service.create(UniversityCreate(name="ЮФУ"))


async def test_update_applies_only_sent_fields(session, scope):
    service = UniversityService(session, scope)
    university = await service.create(
        UniversityCreate(name="СПбПУ", region="г. Санкт-Петербург", comment="исходный")
    )
    updated = await service.update(university.id, UniversityUpdate(comment="новый"))
    assert updated.comment == "новый"
    assert updated.region == "г. Санкт-Петербург"


async def test_find_or_create_is_idempotent(session, scope):
    service = UniversityService(session, scope)
    first, created_first = await service.find_or_create("Бауманка")
    second, created_second = await service.find_or_create("  бауманка  ")
    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_empty_name_is_rejected(session, scope):
    service = UniversityService(session, scope)
    with pytest.raises(ValidationError):
        await service.find_or_create("   ")


async def test_suggest_similar_finds_close_name(session, scope):
    service = UniversityService(session, scope)
    await service.create(UniversityCreate(name="Казанский федеральный университет"))
    suggestions = await service.suggest_similar("Казанский федеральный универститет")
    assert suggestions
    assert suggestions[0][0].name == "Казанский федеральный университет"
    assert suggestions[0][1] > 0.8


async def test_vendor_crud(session, scope):
    service = VendorService(session, scope)
    vendor = await service.create(VendorCreate(name="Астра"))
    assert vendor.name == "Астра"

    with pytest.raises(DuplicateEntityError):
        await service.create(VendorCreate(name="астра"))

    renamed = await service.update(vendor.id, VendorUpdate(name="Группа Астра"))
    assert renamed.name == "Группа Астра"

    _, total = await service.list(search="Астра")
    assert total == 1

    await service.delete(vendor.id)
    _, total_after = await service.list()
    assert total_after == 0


async def test_direction_crud(session, scope):
    service = ITDirectionService(session, scope)
    direction = await service.create(DirectionCreate(name="DevOps", description="CI/CD"))
    assert direction.is_active is True

    updated = await service.update(direction.id, DirectionUpdate(is_active=False))
    assert updated.is_active is False

    again, created = await service.find_or_create("devops")
    assert created is False
    assert again.id == direction.id

    await service.delete(direction.id)
    _, total = await service.list()
    assert total == 0


async def test_product_identity_is_name_plus_vendor(session, scope):
    vendors = VendorService(session, scope)
    astra = await vendors.create(VendorCreate(name="Астра"))
    red = await vendors.create(VendorCreate(name="Ред Софт"))

    products = ITProductService(session, scope)
    first = await products.create(ProductCreate(name="ОС", vendor_id=astra.id))
    second = await products.create(ProductCreate(name="ОС", vendor_id=red.id))
    assert first.id != second.id

    with pytest.raises(DuplicateEntityError):
        await products.create(ProductCreate(name="ос", vendor_id=astra.id))


async def test_product_without_vendor_cannot_be_duplicated(session, scope):
    products = ITProductService(session, scope)
    await products.create(ProductCreate(name="Безымянное ПО"))
    with pytest.raises(DuplicateEntityError):
        await products.create(ProductCreate(name="Безымянное ПО"))


async def test_product_directions_are_replaced_on_update(session, scope):
    directions = ITDirectionService(session, scope)
    devops = await directions.create(DirectionCreate(name="DevOps"))
    qa = await directions.create(DirectionCreate(name="QA"))

    products = ITProductService(session, scope)
    product = await products.create(ProductCreate(name="Инструмент", direction_ids=[devops.id]))
    assert [d.id for d in product.directions] == [devops.id]

    updated = await products.update(product.id, ProductUpdate(direction_ids=[qa.id]))
    assert [d.id for d in updated.directions] == [qa.id]


async def test_product_update_rejects_unknown_direction(session, scope):
    import uuid

    products = ITProductService(session, scope)
    product = await products.create(ProductCreate(name="Инструмент"))
    with pytest.raises(NotFoundError):
        await products.update(product.id, ProductUpdate(direction_ids=[uuid.uuid4()]))


async def test_contact_crud(session, scope):
    universities = UniversityService(session, scope)
    university = await universities.create(UniversityCreate(name="МГУ"))

    contacts = UniversityContactService(session, scope)
    contact = await contacts.create(
        university.id,
        ContactCreate(full_name="Иванова Мария", position="Проректор", email="m@example.edu"),
    )
    assert contact.university_id == university.id
    contact_id = contact.id

    with pytest.raises(DuplicateEntityError):
        await contacts.create(university.id, ContactCreate(full_name="Иванова Мария"))

    updated = await contacts.update(contact_id, ContactUpdate(phone="+7 495 000-00-00"))
    assert updated.phone == "+7 495 000-00-00"
    assert updated.email == "m@example.edu"

    items, total = await contacts.list(university_id=university.id)
    assert total == 1 and items[0].id == contact_id

    found, created = await contacts.find_or_create(university.id, "Иванова Мария")
    assert created is False and found.id == contact_id

    await contacts.delete(contact_id)
    _, total_after = await contacts.list(university_id=university.id)
    assert total_after == 0


async def test_api_product_create_and_vendor_change_return_fresh_vendor(
    session, client, manager_user, scope
):
    """Regression: the embedded vendor must match `vendor_id`.

    Creating a product used to fail with `MissingGreenlet` (relationship never
    loaded), and changing `vendor_id` used to return the *previous* vendor,
    because a plain re-select does not overwrite an already loaded relation.
    """
    from tests.conftest import auth

    astra = await VendorService(session, scope).create(VendorCreate(name="Астра"))
    postgres = await VendorService(session, scope).create(
        VendorCreate(name="Postgres Professional")
    )

    created = await client.post(
        "/api/v1/it-products",
        json={"name": "Проверочное ПО", "vendor_id": str(astra.id)},
        headers=auth(manager_user),
    )
    assert created.status_code == 201
    assert created.json()["vendor"]["name"] == "Астра"

    patched = await client.patch(
        f"/api/v1/it-products/{created.json()['id']}",
        json={"vendor_id": str(postgres.id)},
        headers=auth(manager_user),
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["vendor_id"] == str(postgres.id)
    assert body["vendor"]["name"] == "Postgres Professional"


async def test_api_assignment_create_returns_employee(session, client, manager_user, kam_user):
    """Regression: the embedded employee must be loaded on a new assignment."""
    from tests.conftest import auth

    university = await UniversityService(session, AccessScope.system()).create(
        UniversityCreate(name="МГТУ")
    )
    response = await client.post(
        f"/api/v1/universities/{university.id}/assignments",
        json={"user_id": str(kam_user.id), "assigned_from": "2026-01-01"},
        headers=auth(manager_user),
    )
    assert response.status_code == 201
    assert response.json()["user"]["full_name"] == kam_user.full_name


async def test_referenced_university_cannot_be_deleted(session, scope):
    """Regression: a soft-deleted university kept showing up inside its cards.

    Blocking the delete mirrors the `ON DELETE RESTRICT` on the foreign key and
    keeps the "deleted means invisible" promise honest.
    """
    from app.core.errors import ValidationError as AppValidationError
    from app.schemas.interaction import InteractionCreate
    from app.services.interactions import InteractionService

    universities = UniversityService(session, scope)
    university = await universities.create(UniversityCreate(name="Вуз со связями"))
    interactions = InteractionService(session, scope)
    interaction = await interactions.create(InteractionCreate(university_id=university.id))

    with pytest.raises(AppValidationError) as excinfo:
        await universities.delete(university.id)
    assert excinfo.value.details["blocked_by"]["взаимодействия"] == 1

    # Once the card is gone, the university can be removed.
    await interactions.delete(interaction.id)
    await universities.delete(university.id)
    _, total = await universities.list()
    assert total == 0


async def test_referenced_vendor_and_product_cannot_be_deleted(session, scope):
    from app.core.errors import ValidationError as AppValidationError
    from app.schemas.interaction import InteractionCreate
    from app.services.interactions import InteractionService

    vendors = VendorService(session, scope)
    vendor = await vendors.create(VendorCreate(name="Связанный вендор"))
    products = ITProductService(session, scope)
    product = await products.create(ProductCreate(name="Связанное ПО", vendor_id=vendor.id))

    with pytest.raises(AppValidationError):
        await vendors.delete(vendor.id)

    university = await UniversityService(session, scope).create(
        UniversityCreate(name="Вуз для связи")
    )
    await InteractionService(session, scope).create(
        InteractionCreate(university_id=university.id, it_product_id=product.id)
    )
    with pytest.raises(AppValidationError):
        await products.delete(product.id)


async def test_referenced_direction_cannot_be_deleted(session, scope):
    from app.core.errors import ValidationError as AppValidationError

    directions = ITDirectionService(session, scope)
    direction = await directions.create(DirectionCreate(name="Связанное направление"))
    await ITProductService(session, scope).create(
        ProductCreate(name="ПО направления", direction_ids=[direction.id])
    )

    with pytest.raises(AppValidationError) as excinfo:
        await directions.delete(direction.id)
    assert excinfo.value.details["blocked_by"]["ИТ-продукты"] == 1
