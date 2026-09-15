"""Repository for the local Keycloak user projection."""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User
    searchable_fields: ClassVar[tuple[str, ...]] = ("full_name", "email")
    sortable_fields: ClassVar[tuple[str, ...]] = ("full_name", "role", "created_at", "updated_at")
    default_order: ClassVar[tuple[str, ...]] = ("full_name",)

    async def find_by_keycloak_id(self, keycloak_id: str) -> User | None:
        return await self.session.scalar(
            sa.select(User).where(User.keycloak_id == keycloak_id, User.deleted_at.is_(None))
        )

    async def find_by_normalized_full_name(self, normalized: str) -> User | None:
        """Exact fold match for the «ФИО Менеджера» Excel column.

        Returns ``None`` when the fold matches more than one employee: an
        ambiguous match must become a warning, never a silent pick.
        """
        rows = list(
            (
                await self.session.scalars(
                    sa.select(User).where(
                        User.full_name_normalized == normalized,
                        User.deleted_at.is_(None),
                        User.is_active.is_(True),
                    )
                )
            ).all()
        )
        return rows[0] if len(rows) == 1 else None
