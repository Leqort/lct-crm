"""Interaction business logic, including the upsert used by the importer."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import ValidationError
from app.models.interaction import Interaction
from app.repositories.interaction import InteractionRepository
from app.repositories.university import UniversityRepository
from app.schemas.interaction import InteractionCreate, InteractionUpdate
from app.services.base import apply_patch, integrity_guard
from app.services.reporting import period_conditions
from app.services.route import RouteService
from app.services.text import clean_text

# Fields the importer is allowed to write on an interaction.
IMPORTABLE_FIELDS = (
    "responsible_user_id",
    "contract_number",
    "license_signed_at",
    "license_years",
    "license_expires_at",
    "transfer_status",
    "comment",
)


def compute_license_expiry(
    signed_at: dt.date | None, years: int | None, explicit: dt.date | None = None
) -> dt.date | None:
    """Derive `license_expires_at` (SPEC §4.3).

    An explicitly supplied date always wins. Otherwise the expiry is
    "signed_at + N years". 29 February is clamped to 28 February, because
    `date.replace(year=...)` would otherwise raise on non-leap target years.
    """
    if explicit is not None:
        return explicit
    if signed_at is None or years is None:
        return None
    try:
        return signed_at.replace(year=signed_at.year + years)
    except ValueError:
        return signed_at.replace(year=signed_at.year + years, day=28)


class InteractionService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = InteractionRepository(session, self.scope)
        self.universities = UniversityRepository(session, self.scope)

    async def list(
        self,
        *,
        university_id: uuid.UUID | None = None,
        it_direction_id: uuid.UUID | None = None,
        it_product_id: uuid.UUID | None = None,
        responsible_user_id: uuid.UUID | None = None,
        period_from: dt.date | None = None,
        period_to: dt.date | None = None,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        sort: str | None = None,
    ) -> tuple[list[Interaction], int]:
        filters = []
        if university_id is not None:
            filters.append(Interaction.university_id == university_id)
        if it_direction_id is not None:
            filters.append(Interaction.it_direction_id == it_direction_id)
        if it_product_id is not None:
            filters.append(Interaction.it_product_id == it_product_id)
        if responsible_user_id is not None:
            filters.append(Interaction.responsible_user_id == responsible_user_id)
        filters.extend(period_conditions(period_from, period_to))
        return await self.repo.list(
            page=page, size=size, search=search, sort=sort, extra_filters=filters
        )

    async def get(self, interaction_id: uuid.UUID) -> Interaction:
        return await self.repo.get_or_fail(interaction_id)

    async def create(self, data: InteractionCreate) -> Interaction:
        await self.universities.get_or_fail(data.university_id)
        interaction = Interaction(
            university_id=data.university_id,
            it_direction_id=data.it_direction_id,
            it_product_id=data.it_product_id,
            responsible_user_id=data.responsible_user_id,
            contract_number=clean_text(data.contract_number),
            license_signed_at=data.license_signed_at,
            license_years=data.license_years,
            license_expires_at=compute_license_expiry(
                data.license_signed_at, data.license_years, data.license_expires_at
            ),
            transfer_status=clean_text(data.transfer_status),
            comment=clean_text(data.comment),
        )
        self.repo.add(interaction)
        async with integrity_guard(
            self.session,
            duplicate_message=(
                "Взаимодействие для этой пары «вуз + направление + продукт» уже существует"
            ),
        ):
            await self.session.flush()
            # A new card joins the default workflow straight away, so it is
            # never stranded outside the process. No-op until one is published.
            await RouteService(self.session, self.scope).start_if_configured(interaction)
            await self.session.commit()
        # The response embeds university/direction/product/responsible, none of
        # which a just-inserted object has loaded.
        return await self.repo.reload(interaction)

    async def update(self, interaction_id: uuid.UUID, data: InteractionUpdate) -> Interaction:
        interaction = await self.repo.get_or_fail(interaction_id)
        patch = data.model_dump(exclude_unset=True)
        if "university_id" in patch:
            await self.universities.get_or_fail(patch["university_id"])
        for field in ("contract_number", "transfer_status", "comment"):
            if field in patch:
                patch[field] = clean_text(patch[field])
        apply_patch(interaction, patch)
        # Recompute the expiry only when one of its inputs was actually part of
        # this PATCH. Recomputing unconditionally would silently replace a
        # manually entered expiry date the moment someone edits the comment.
        recompute = "license_expires_at" not in patch and (
            "license_signed_at" in patch or "license_years" in patch
        )
        if recompute:
            interaction.license_expires_at = compute_license_expiry(
                interaction.license_signed_at, interaction.license_years
            )
        async with integrity_guard(
            self.session,
            duplicate_message=(
                "Взаимодействие для этой пары «вуз + направление + продукт» уже существует"
            ),
        ):
            await self.session.flush()
            await self.session.commit()
        # A changed foreign key leaves the previously loaded relation stale.
        return await self.repo.reload(interaction)

    async def delete(self, interaction_id: uuid.UUID) -> None:
        interaction = await self.repo.get_or_fail(interaction_id)
        await self.repo.soft_delete(interaction)
        await self.session.commit()

    async def upsert(
        self,
        *,
        university_id: uuid.UUID,
        it_direction_id: uuid.UUID | None,
        it_product_id: uuid.UUID | None,
        values: dict[str, Any],
    ) -> tuple[Interaction, bool]:
        """Create-or-update on the business key (SPEC §6 step 4).

        Two rules make this the hard part of the import:

        * duplicates must never appear — the lookup uses exactly the columns of
          the partial unique index, with NULL treated as a value;
        * an empty cell means "no data", not "clear the field", so ``None``
          values never overwrite something already stored.

        Does not commit: the whole import commit is one transaction.
        """
        if not university_id:
            raise ValidationError("Не указан вуз — строка не может быть импортирована")

        interaction = await self.repo.find_by_business_key(
            university_id, it_direction_id, it_product_id
        )
        created = False
        if interaction is None:
            interaction = Interaction(
                university_id=university_id,
                it_direction_id=it_direction_id,
                it_product_id=it_product_id,
            )
            self.repo.add(interaction)
            await self.session.flush()
            # Imported cards enter the process on the same footing as ones
            # created by hand.
            await RouteService(self.session, self.scope).start_if_configured(interaction)
            created = True

        for field in IMPORTABLE_FIELDS:
            if field not in values:
                continue
            new_value = values[field]
            if new_value is None:
                # Empty cell: keep whatever is already in the database.
                continue
            setattr(interaction, field, new_value)

        # Same "empty means no data" rule as above: if neither the signing date
        # nor the term is known, there is nothing to derive from, and clearing a
        # previously stored expiry would be data loss.
        derived = compute_license_expiry(
            interaction.license_signed_at,
            interaction.license_years,
            values.get("license_expires_at"),
        )
        if derived is not None:
            interaction.license_expires_at = derived
        await self.session.flush()
        return interaction, created
