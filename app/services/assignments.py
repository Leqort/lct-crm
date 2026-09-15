"""KAM assignments — the data behind the `user` role's visibility."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.errors import NotFoundError, ValidationError
from app.models.university import UniversityAssignment
from app.models.user import User
from app.repositories.university import UniversityAssignmentRepository, UniversityRepository
from app.schemas.university import AssignmentCreate
from app.services.base import integrity_guard


class AssignmentService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.repo = UniversityAssignmentRepository(session, self.scope)
        self.universities = UniversityRepository(session, self.scope)

    async def list_for_university(
        self, university_id: uuid.UUID, *, page: int = 1, size: int = 50, sort: str | None = None
    ) -> tuple[list[UniversityAssignment], int]:
        await self.universities.get_or_fail(university_id)
        return await self.repo.list(
            page=page,
            size=size,
            sort=sort,
            extra_filters=[UniversityAssignment.university_id == university_id],
        )

    async def create(
        self, university_id: uuid.UUID, data: AssignmentCreate
    ) -> UniversityAssignment:
        await self.universities.get_or_fail(university_id)

        user = await self.session.scalar(
            sa.select(User).where(User.id == data.user_id, User.deleted_at.is_(None))
        )
        if user is None:
            raise NotFoundError("Сотрудник не найден", details={"user_id": str(data.user_id)})
        if not user.is_active:
            raise ValidationError(
                "Нельзя назначить ответственным неактивного сотрудника",
                details={"user_id": str(data.user_id)},
            )

        # SPEC §4.2: one live assignment per university at any point in time.
        # Checked here for a readable message; the GiST exclusion constraint on
        # the table is what actually guarantees it under concurrency.
        overlap = await self.repo.find_overlapping(
            university_id, data.assigned_from, data.assigned_to
        )
        if overlap is not None:
            raise ValidationError(
                "У вуза уже есть ответственный на пересекающийся период",
                details={
                    "conflicting_assignment_id": str(overlap.id),
                    "assigned_from": overlap.assigned_from.isoformat(),
                    "assigned_to": overlap.assigned_to.isoformat() if overlap.assigned_to else None,
                },
            )

        assignment = UniversityAssignment(
            university_id=university_id,
            user_id=data.user_id,
            assigned_from=data.assigned_from,
            assigned_to=data.assigned_to,
        )
        self.repo.add(assignment)
        async with integrity_guard(
            self.session,
            duplicate_message="У вуза уже есть ответственный на пересекающийся период",
        ):
            await self.session.flush()
            await self.session.commit()
        # The response embeds the assigned employee.
        return await self.repo.reload(assignment)

    async def close(
        self, assignment_id: uuid.UUID, *, closed_on: dt.date | None = None
    ) -> UniversityAssignment:
        """`DELETE /assignments/{id}` closes the period; the row stays (SPEC §7)."""
        assignment = await self.repo.get_or_fail(assignment_id)
        end_date = closed_on or dt.date.today()
        if end_date < assignment.assigned_from:
            # Closing before it ever started would produce an invalid period;
            # collapse it to a single day instead of failing the request.
            end_date = assignment.assigned_from
        if assignment.assigned_to is not None and assignment.assigned_to <= end_date:
            return assignment
        assignment.assigned_to = end_date
        await self.session.flush()
        await self.session.commit()
        return await self.repo.reload(assignment)
