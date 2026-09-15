"""Row-level visibility scope (SPEC §8).

Deliberately a tiny value object with no FastAPI/ORM imports: the repository
layer depends on it, so it must not drag the web layer in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


class _UserLike(Protocol):
    id: uuid.UUID

    @property
    def is_privileged(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccessScope:
    """Who is looking, and whether they may look at everything."""

    user_id: uuid.UUID | None
    is_privileged: bool

    @classmethod
    def from_user(cls, user: _UserLike) -> AccessScope:
        return cls(user_id=user.id, is_privileged=user.is_privileged)

    @classmethod
    def system(cls) -> AccessScope:
        """Unrestricted scope for background jobs, seeds and import commits."""
        return cls(user_id=None, is_privileged=True)
