"""Catalog business logic.

SPEC §9 requires every find-or-create and every name-normalisation rule to live
here, not in the Excel parser: the upcoming LMS/website integrations write into
the same catalogs and must behave identically.
"""

from __future__ import annotations

import builtins
import uuid
from typing import Any

import sqlalchemy as sa
from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import DuplicateEntityError, NotFoundError, ValidationError
from app.models.interaction import Interaction
from app.models.product import ITDirection, ITProduct, ITProductDirection, Vendor
from app.models.university import University, UniversityAssignment, UniversityContact
from app.repositories.product import (
    ITDirectionRepository,
    ITProductRepository,
    VendorRepository,
)
from app.repositories.university import UniversityContactRepository, UniversityRepository
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
from app.services.base import apply_patch, assert_not_referenced, count_live, integrity_guard
from app.services.text import clean_text, normalize_name

# Trigram similarity is used only to shortlist candidates for the edit-distance
# re-ranking below, so the bar here is deliberately low.
_TRIGRAM_CANDIDATE_THRESHOLD = 0.3


class UniversityService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = UniversityRepository(session, self.scope)

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[University], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, university_id: uuid.UUID) -> University:
        return await self.repo.get_or_fail(university_id)

    async def create(self, data: UniversityCreate) -> University:
        name = clean_text(data.name)
        if not name:
            raise ValidationError("Название вуза не может быть пустым")
        normalized = normalize_name(name)
        if await self.repo.find_by_normalized_name(normalized):
            raise DuplicateEntityError(
                "Вуз с таким названием уже существует", details={"name": name}
            )
        university = University(
            name=name,
            name_normalized=normalized,
            short_name=clean_text(data.short_name),
            region=clean_text(data.region),
            inn=clean_text(data.inn),
            external_id=clean_text(data.external_id),
            comment=clean_text(data.comment),
        )
        self.repo.add(university)
        async with integrity_guard(
            self.session, duplicate_message="Вуз с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return university

    async def update(self, university_id: uuid.UUID, data: UniversityUpdate) -> University:
        university = await self.repo.get_or_fail(university_id)
        patch = data.model_dump(exclude_unset=True)
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название вуза не может быть пустым")
            patch["name"] = name
            patch["name_normalized"] = normalize_name(name)
        for field in ("short_name", "region", "inn", "external_id", "comment"):
            if field in patch:
                patch[field] = clean_text(patch[field])

        apply_patch(university, patch)
        async with integrity_guard(
            self.session, duplicate_message="Вуз с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return university

    async def delete(self, university_id: uuid.UUID) -> None:
        university = await self.repo.get_or_fail(university_id)
        await assert_not_referenced(
            self.session,
            "вуз",
            {
                "взаимодействия": await count_live(
                    self.session, Interaction, Interaction.university_id == university_id
                ),
                "контактные лица": await count_live(
                    self.session,
                    UniversityContact,
                    UniversityContact.university_id == university_id,
                ),
                "назначения ответственных": await count_live(
                    self.session,
                    UniversityAssignment,
                    UniversityAssignment.university_id == university_id,
                ),
            },
        )
        await self.repo.soft_delete(university)
        await self.session.commit()

    async def find_or_create(
        self, name: str, *, defaults: dict[str, Any] | None = None
    ) -> tuple[University, bool]:
        """Resolve a university by name, creating it when unknown.

        Does **not** commit: callers (import commit, integrations) own the
        transaction. Returns `(entity, created)`.
        """
        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название вуза не может быть пустым")
        normalized = normalize_name(cleaned)
        existing = await self.repo.find_by_normalized_name(normalized)
        if existing is not None:
            return existing, False
        university = University(
            name=cleaned,
            name_normalized=normalized,
            **{k: clean_text(v) for k, v in (defaults or {}).items()},
        )
        self.repo.add(university)
        await self.session.flush()
        return university, True

    async def suggest_similar(
        self, name: str, *, limit: int = 3
    ) -> builtins.list[tuple[University, float]]:
        """Rank existing universities by how close their name is to `name`.

        Two stages on purpose. PostgreSQL trigram similarity is cheap and index
        backed, so it fetches a handful of candidates; it is however a poor
        *score* for human typos — a single transposed letter costs it far more
        than it costs a reader. The shortlist is therefore re-ranked with an
        edit-distance ratio, and that is the number compared against
        `IMPORT_FUZZY_THRESHOLD`.
        """
        normalized = normalize_name(name)
        if not normalized:
            return []
        candidates = await self.repo.find_similar(
            normalized, limit=max(limit * 5, 10), threshold=_TRIGRAM_CANDIDATE_THRESHOLD
        )
        rescored = [
            (university, fuzz.ratio(normalized, university.name_normalized) / 100.0)
            for university, _ in candidates
        ]
        rescored.sort(key=lambda item: item[1], reverse=True)
        return rescored[:limit]


class VendorService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = VendorRepository(session, self.scope)

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[Vendor], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, vendor_id: uuid.UUID) -> Vendor:
        return await self.repo.get_or_fail(vendor_id)

    async def create(self, data: VendorCreate) -> Vendor:
        vendor, created = await self.find_or_create(data.name)
        if not created:
            raise DuplicateEntityError(
                "Вендор с таким названием уже существует", details={"name": data.name}
            )
        async with integrity_guard(
            self.session, duplicate_message="Вендор с таким названием уже существует"
        ):
            await self.session.commit()
        return vendor

    async def update(self, vendor_id: uuid.UUID, data: VendorUpdate) -> Vendor:
        vendor = await self.repo.get_or_fail(vendor_id)
        patch = data.model_dump(exclude_unset=True)
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название вендора не может быть пустым")
            patch["name"] = name
            patch["name_normalized"] = normalize_name(name)
        apply_patch(vendor, patch)
        async with integrity_guard(
            self.session, duplicate_message="Вендор с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return vendor

    async def delete(self, vendor_id: uuid.UUID) -> None:
        vendor = await self.repo.get_or_fail(vendor_id)
        await assert_not_referenced(
            self.session,
            "вендора",
            {
                "ИТ-продукты": await count_live(
                    self.session, ITProduct, ITProduct.vendor_id == vendor_id
                )
            },
        )
        await self.repo.soft_delete(vendor)
        await self.session.commit()

    async def find_or_create(self, name: str) -> tuple[Vendor, bool]:
        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название вендора не может быть пустым")
        normalized = normalize_name(cleaned)
        existing = await self.repo.find_by_normalized_name(normalized)
        if existing is not None:
            return existing, False
        vendor = Vendor(name=cleaned, name_normalized=normalized)
        self.repo.add(vendor)
        await self.session.flush()
        return vendor, True


class ITDirectionService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = ITDirectionRepository(session, self.scope)

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[ITDirection], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, direction_id: uuid.UUID) -> ITDirection:
        return await self.repo.get_or_fail(direction_id)

    async def create(self, data: DirectionCreate) -> ITDirection:
        name = clean_text(data.name)
        if not name:
            raise ValidationError("Название направления не может быть пустым")
        if await self.repo.find_by_normalized_name(normalize_name(name)):
            raise DuplicateEntityError(
                "ИТ-направление с таким названием уже существует", details={"name": name}
            )
        direction = ITDirection(
            name=name,
            name_normalized=normalize_name(name),
            description=clean_text(data.description),
            is_active=data.is_active,
        )
        self.repo.add(direction)
        async with integrity_guard(
            self.session, duplicate_message="ИТ-направление с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return direction

    async def update(self, direction_id: uuid.UUID, data: DirectionUpdate) -> ITDirection:
        direction = await self.repo.get_or_fail(direction_id)
        patch = data.model_dump(exclude_unset=True)
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название направления не может быть пустым")
            patch["name"] = name
            patch["name_normalized"] = normalize_name(name)
        if "description" in patch:
            patch["description"] = clean_text(patch["description"])
        apply_patch(direction, patch)
        async with integrity_guard(
            self.session, duplicate_message="ИТ-направление с таким названием уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return direction

    async def delete(self, direction_id: uuid.UUID) -> None:
        direction = await self.repo.get_or_fail(direction_id)
        linked_products = await self.session.scalar(
            sa.select(sa.func.count())
            .select_from(ITProduct)
            .join(ITProductDirection, ITProductDirection.it_product_id == ITProduct.id)
            .where(
                ITProductDirection.it_direction_id == direction_id,
                ITProduct.deleted_at.is_(None),
            )
        )
        await assert_not_referenced(
            self.session,
            "ИТ-направление",
            {
                "взаимодействия": await count_live(
                    self.session, Interaction, Interaction.it_direction_id == direction_id
                ),
                "ИТ-продукты": int(linked_products or 0),
            },
        )
        await self.repo.soft_delete(direction)
        await self.session.commit()

    async def find_or_create(self, name: str) -> tuple[ITDirection, bool]:
        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название направления не может быть пустым")
        normalized = normalize_name(cleaned)
        existing = await self.repo.find_by_normalized_name(normalized)
        if existing is not None:
            return existing, False
        direction = ITDirection(name=cleaned, name_normalized=normalized)
        self.repo.add(direction)
        await self.session.flush()
        return direction, True


class ITProductService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = ITProductRepository(session, self.scope)
        self.directions = ITDirectionRepository(session, self.scope)

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[ITProduct], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, product_id: uuid.UUID) -> ITProduct:
        return await self.repo.get_or_fail(product_id)

    async def _load_directions(self, ids: builtins.list[uuid.UUID]) -> builtins.list[ITDirection]:
        if not ids:
            return []
        rows = list(
            (
                await self.session.scalars(
                    sa.select(ITDirection).where(
                        ITDirection.id.in_(ids), ITDirection.deleted_at.is_(None)
                    )
                )
            ).all()
        )
        missing: set[uuid.UUID] = set(ids) - {row.id for row in rows}
        if missing:
            raise NotFoundError(
                "Часть ИТ-направлений не найдена",
                details={"missing_direction_ids": [str(i) for i in missing]},
            )
        return rows

    async def create(self, data: ProductCreate) -> ITProduct:
        name = clean_text(data.name)
        if not name:
            raise ValidationError("Название продукта не может быть пустым")
        normalized = normalize_name(name)
        if await self.repo.find_by_name_and_vendor(normalized, data.vendor_id):
            raise DuplicateEntityError(
                "Продукт с таким названием у этого вендора уже существует",
                details={
                    "name": name,
                    "vendor_id": str(data.vendor_id) if data.vendor_id else None,
                },
            )
        product = ITProduct(
            name=name,
            name_normalized=normalized,
            vendor_id=data.vendor_id,
            description=clean_text(data.description),
            is_active=data.is_active,
        )
        product.directions = await self._load_directions(data.direction_ids)
        self.repo.add(product)
        async with integrity_guard(
            self.session,
            duplicate_message="Продукт с таким названием у этого вендора уже существует",
        ):
            await self.session.flush()
            await self.session.commit()
        # The response embeds the vendor, which a new object has not loaded.
        return await self.repo.reload(product)

    async def update(self, product_id: uuid.UUID, data: ProductUpdate) -> ITProduct:
        product = await self.repo.get_or_fail(product_id)
        patch = data.model_dump(exclude_unset=True)
        direction_ids = patch.pop("direction_ids", None)
        if "name" in patch:
            name = clean_text(patch["name"])
            if not name:
                raise ValidationError("Название продукта не может быть пустым")
            patch["name"] = name
            patch["name_normalized"] = normalize_name(name)
        if "description" in patch:
            patch["description"] = clean_text(patch["description"])
        apply_patch(product, patch)
        if direction_ids is not None:
            product.directions = await self._load_directions(direction_ids)
        async with integrity_guard(
            self.session,
            duplicate_message="Продукт с таким названием у этого вендора уже существует",
        ):
            await self.session.flush()
            await self.session.commit()
        # Changing `vendor_id` does not by itself refresh the loaded `vendor`.
        return await self.repo.reload(product)

    async def delete(self, product_id: uuid.UUID) -> None:
        product = await self.repo.get_or_fail(product_id)
        await assert_not_referenced(
            self.session,
            "ИТ-продукт",
            {
                "взаимодействия": await count_live(
                    self.session, Interaction, Interaction.it_product_id == product_id
                )
            },
        )
        await self.repo.soft_delete(product)
        await self.session.commit()

    async def find_or_create(
        self, name: str, *, vendor_id: uuid.UUID | None = None
    ) -> tuple[ITProduct, bool]:
        """Product identity is (name, vendor) — matching the unique index."""
        cleaned = clean_text(name)
        if not cleaned:
            raise ValidationError("Название продукта не может быть пустым")
        normalized = normalize_name(cleaned)
        existing = await self.repo.find_by_name_and_vendor(normalized, vendor_id)
        if existing is not None:
            return existing, False
        product = ITProduct(name=cleaned, name_normalized=normalized, vendor_id=vendor_id)
        # Initialise the collection explicitly: once the object is flushed it
        # becomes persistent, and an untouched collection would then try to
        # lazy-load — which is illegal outside an await in async SQLAlchemy.
        product.directions = []
        self.repo.add(product)
        await self.session.flush()
        return product, True


class UniversityContactService:
    """Contacts carry personal data — reads are audited (SPEC §5.4)."""

    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = UniversityContactRepository(session, self.scope)
        self.universities = UniversityRepository(session, self.scope)

    async def list(
        self,
        *,
        university_id: uuid.UUID | None = None,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        sort: str | None = None,
    ) -> tuple[list[UniversityContact], int]:
        filters = []
        if university_id is not None:
            # Access check first: asking for a foreign university's contacts is
            # a refusal, not an empty page.
            await self.universities.get_or_fail(university_id)
            filters.append(UniversityContact.university_id == university_id)
        return await self.repo.list(
            page=page, size=size, search=search, sort=sort, extra_filters=filters
        )

    async def get(self, contact_id: uuid.UUID) -> UniversityContact:
        return await self.repo.get_or_fail(contact_id)

    async def create(self, university_id: uuid.UUID, data: ContactCreate) -> UniversityContact:
        await self.universities.get_or_fail(university_id)
        full_name = clean_text(data.full_name)
        if not full_name:
            raise ValidationError("ФИО контактного лица не может быть пустым")
        if await self.repo.find_by_name(university_id, full_name):
            raise DuplicateEntityError(
                "Контактное лицо с таким ФИО уже заведено у этого вуза",
                details={"full_name": full_name},
            )
        contact = UniversityContact(
            university_id=university_id,
            full_name=full_name,
            position=clean_text(data.position),
            email=clean_text(data.email),
            phone=clean_text(data.phone),
            is_primary=data.is_primary,
        )
        self.repo.add(contact)
        async with integrity_guard(
            self.session,
            duplicate_message="Контактное лицо с таким ФИО уже заведено у этого вуза",
        ):
            await self.session.flush()
            await self.session.commit()
        return contact

    async def update(self, contact_id: uuid.UUID, data: ContactUpdate) -> UniversityContact:
        contact = await self.repo.get_or_fail(contact_id)
        patch = data.model_dump(exclude_unset=True)
        for field in ("full_name", "position", "email", "phone"):
            if field in patch:
                patch[field] = clean_text(patch[field])
        if "full_name" in patch and not patch["full_name"]:
            raise ValidationError("ФИО контактного лица не может быть пустым")
        apply_patch(contact, patch)
        async with integrity_guard(
            self.session,
            duplicate_message="Контактное лицо с таким ФИО уже заведено у этого вуза",
        ):
            await self.session.flush()
            await self.session.commit()
        return contact

    async def delete(self, contact_id: uuid.UUID) -> None:
        contact = await self.repo.get_or_fail(contact_id)
        await self.repo.soft_delete(contact)
        await self.session.commit()

    async def find_or_create(
        self, university_id: uuid.UUID, full_name: str
    ) -> tuple[UniversityContact, bool]:
        cleaned = clean_text(full_name)
        if not cleaned:
            raise ValidationError("ФИО контактного лица не может быть пустым")
        existing = await self.repo.find_by_name(university_id, cleaned)
        if existing is not None:
            return existing, False
        contact = UniversityContact(university_id=university_id, full_name=cleaned)
        self.repo.add(contact)
        await self.session.flush()
        return contact, True
