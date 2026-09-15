"""User projection service — used by seeds, assignments and the import matcher."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import DuplicateEntityError, ValidationError
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate
from app.services.base import integrity_guard
from app.services.text import clean_text, normalize_person_name


class UserService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = UserRepository(session, self.scope)

    async def list(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[User], int]:
        return await self.repo.list(page=page, size=size, search=search, sort=sort)

    async def get(self, user_id: uuid.UUID) -> User:
        return await self.repo.get_or_fail(user_id)

    async def create(self, data: UserCreate) -> User:
        full_name = clean_text(data.full_name)
        if not full_name:
            raise ValidationError("ФИО сотрудника не может быть пустым")
        if await self.repo.find_by_keycloak_id(data.keycloak_id):
            raise DuplicateEntityError(
                "Сотрудник с таким keycloak_id уже существует",
                details={"keycloak_id": data.keycloak_id},
            )
        user = User(
            keycloak_id=data.keycloak_id,
            full_name=full_name,
            full_name_normalized=normalize_person_name(full_name),
            email=clean_text(data.email),
            role=data.role,
            is_active=data.is_active,
        )
        self.repo.add(user)
        async with integrity_guard(
            self.session, duplicate_message="Сотрудник с таким keycloak_id уже существует"
        ):
            await self.session.flush()
            await self.session.commit()
        return user

    async def match_by_full_name(self, full_name: str) -> User | None:
        """Resolve «ФИО Менеджера» from Excel to an employee.

        Returns ``None`` when nothing matches *or* when the name is ambiguous;
        the importer turns that into a warning and leaves the field empty
        (SPEC §6 step 3).
        """
        cleaned = clean_text(full_name)
        if not cleaned:
            return None
        return await self.repo.find_by_normalized_full_name(normalize_person_name(cleaned))
