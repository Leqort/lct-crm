"""Generic data access with soft delete, access scoping, paging and sorting.

The access filter lives *here* on purpose (SPEC §8): a router cannot forget to
apply it, because every read goes through `_base_select`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.access import AccessScope
from app.core.config import settings
from app.core.errors import AccessDeniedError, NotFoundError, ValidationError
from app.models.base import Base
from app.models.university import UniversityAssignment


def visible_university_ids(scope: AccessScope) -> Select[tuple[uuid.UUID]]:
    """Universities the scope's user is currently assigned to.

    "Currently" means today falls inside `[assigned_from, assigned_to]`, with a
    NULL upper bound meaning "still in effect".
    """
    today = sa.func.current_date()
    return sa.select(UniversityAssignment.university_id).where(
        UniversityAssignment.user_id == scope.user_id,
        UniversityAssignment.deleted_at.is_(None),
        UniversityAssignment.assigned_from <= today,
        sa.or_(
            UniversityAssignment.assigned_to.is_(None),
            UniversityAssignment.assigned_to >= today,
        ),
    )


class BaseRepository[ModelT: Base]:
    model: type[ModelT]
    # Columns matched by the `search` query parameter (case-insensitive).
    searchable_fields: ClassVar[tuple[str, ...]] = ()
    # Whitelist for `sort`; anything else is a client error, never silently
    # ignored — silent ignoring makes paging look broken.
    sortable_fields: ClassVar[tuple[str, ...]] = ("created_at",)
    default_order: ClassVar[tuple[str, ...]] = ("-created_at",)
    # Column used to restrict rows to the universities the user may see.
    # `None` means the table is a global catalog visible to everyone.
    university_scope_column: ClassVar[str | None] = None

    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()

    # --- query building ----------------------------------------------------

    def _soft_delete_filter(self, stmt: Select[Any]) -> Select[Any]:
        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is not None:
            stmt = stmt.where(deleted_at.is_(None))
        return stmt

    def _access_filter(self, stmt: Select[Any]) -> Select[Any]:
        if self.scope.is_privileged or self.university_scope_column is None:
            return stmt
        column = getattr(self.model, self.university_scope_column)
        return stmt.where(column.in_(visible_university_ids(self.scope)))

    def _base_select(self, *, apply_access: bool = True) -> Select[Any]:
        stmt = sa.select(self.model)
        stmt = self._soft_delete_filter(stmt)
        if apply_access:
            stmt = self._access_filter(stmt)
        return stmt

    def _apply_search(self, stmt: Select[Any], search: str | None) -> Select[Any]:
        if not search or not self.searchable_fields:
            return stmt
        pattern = f"%{search.strip()}%"
        clauses = [
            getattr(self.model, name).ilike(pattern)
            for name in self.searchable_fields
            if hasattr(self.model, name)
        ]
        return stmt.where(sa.or_(*clauses)) if clauses else stmt

    def _apply_sort(self, stmt: Select[Any], sort: str | None) -> Select[Any]:
        """Parse `sort=field,-other` into ORDER BY clauses."""
        raw = [part.strip() for part in (sort or "").split(",") if part.strip()]
        if not raw:
            raw = list(self.default_order)

        order_by = []
        for item in raw:
            descending = item.startswith("-")
            name = item.lstrip("-+")
            if name not in self.sortable_fields:
                raise ValidationError(
                    f"Сортировка по полю «{name}» не поддерживается",
                    details={"field": name, "allowed": list(self.sortable_fields)},
                )
            column = getattr(self.model, name)
            order_by.append(column.desc() if descending else column.asc())
        # Stable tie-breaker so paging cannot repeat or skip rows.
        pk = getattr(self.model, "id", None)
        if pk is not None:
            order_by.append(pk.asc())
        return stmt.order_by(*order_by)

    # --- reads -------------------------------------------------------------

    async def get(self, entity_id: uuid.UUID, *, apply_access: bool = True) -> ModelT | None:
        stmt = self._base_select(apply_access=apply_access).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return await self.session.scalar(stmt)

    async def get_or_fail(self, entity_id: uuid.UUID) -> ModelT:
        """Fetch by id, distinguishing "absent" from "not yours" (SPEC §8).

        Returning 404 for a foreign object would hide the refusal from the
        audit trail, which is explicitly not what the customer wants.
        """
        obj = await self.get(entity_id, apply_access=True)
        if obj is not None:
            return obj

        exists = await self.get(entity_id, apply_access=False)
        if exists is None:
            raise NotFoundError(
                "Объект не найден",
                details={"entity_type": self.model.__tablename__, "id": str(entity_id)},
            )

        from app.services.audit import log_access_denied

        await log_access_denied(
            self.session,
            entity_type=self.model.__tablename__,
            entity_id=entity_id,
            reason="out_of_scope",
        )
        raise AccessDeniedError(
            "Объект относится к вузу, который вам не назначен",
            details={"entity_type": self.model.__tablename__, "id": str(entity_id)},
        )

    async def list(
        self,
        *,
        page: int = 1,
        size: int | None = None,
        search: str | None = None,
        sort: str | None = None,
        extra_filters: list[Any] | None = None,
    ) -> tuple[list[ModelT], int]:
        size = size or settings.page_size_default
        size = min(size, settings.page_size_max)
        page = max(page, 1)

        stmt = self._base_select()
        if extra_filters:
            stmt = stmt.where(*extra_filters)
        stmt = self._apply_search(stmt, search)

        count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
        total = int(await self.session.scalar(count_stmt) or 0)

        stmt = self._apply_sort(stmt, sort).offset((page - 1) * size).limit(size)
        items = list((await self.session.scalars(stmt)).unique().all())
        return items, total

    # --- writes ------------------------------------------------------------

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        return obj

    async def reload(self, obj: ModelT) -> ModelT:
        """Re-read an object so its eager relationships are correct.

        Needed after create and after update, for two different reasons:

        * a freshly inserted object has no loaded relationships at all, and
          touching one would emit a lazy SELECT — illegal outside an await;
        * after a foreign key changes, the previously loaded related object is
          *stale*: a plain SELECT would return the identity-mapped instance
          without overwriting it, so the response would show the old vendor.

        `populate_existing` is what forces both cases to refresh.
        """
        stmt = (
            sa.select(self.model)
            .where(self.model.id == obj.id)  # type: ignore[attr-defined]
            .execution_options(populate_existing=True)
        )
        reloaded = await self.session.scalar(stmt)
        return reloaded if reloaded is not None else obj

    async def soft_delete(self, obj: ModelT) -> ModelT:
        """The only deletion this system performs (SPEC §4.1)."""
        obj.deleted_at = dt.datetime.now(dt.UTC)  # type: ignore[attr-defined]
        await self.session.flush()
        return obj
