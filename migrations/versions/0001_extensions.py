"""Required PostgreSQL extensions

pg_trgm    — trigram similarity for the "did you mean ...?" university matcher.
btree_gist — lets the GiST exclusion constraint on `university_assignments`
             combine an equality check on a UUID with a range overlap check.

Revision ID: 0001_extensions
Revises:
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def downgrade() -> None:
    # Dropping a shared extension can break unrelated objects in the same
    # database, so downgrade deliberately leaves them in place.
    pass
