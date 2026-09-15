"""Local projection of a Keycloak user (SPEC §4.2).

Keycloak stays the master; we keep just enough here to build foreign keys and
render full names in lists without a round-trip to the IdP.
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DomainBase
from app.models.enums import USER_ROLE_ENUM, UserRole


class User(DomainBase):
    __tablename__ = "users"

    __pd_fields__: ClassVar[frozenset[str]] = frozenset({"full_name", "email"})

    keycloak_id: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Folded full name — the Excel "ФИО Менеджера" column is matched against it.
    full_name_normalized: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str | None] = mapped_column(sa.Text, default=None)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(UserRole, name=USER_ROLE_ENUM, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=UserRole.USER,
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    __table_args__ = (
        sa.Index("ix_users_full_name_normalized", "full_name_normalized"),
        sa.Index(
            "ix_users_full_name_normalized_trgm",
            "full_name_normalized",
            postgresql_using="gin",
            postgresql_ops={"full_name_normalized": "gin_trgm_ops"},
        ),
    )
