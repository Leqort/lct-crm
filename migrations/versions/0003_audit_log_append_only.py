"""Make audit_log append-only

SPEC §4.5 requires UPDATE and DELETE on the audit trail to be impossible. Two
independent layers are used:

1. `REVOKE UPDATE, DELETE` — the declarative, grant-based answer the spec asks
   for. It binds every non-superuser role.
2. A `BEFORE UPDATE OR DELETE` trigger — grants do not apply to superusers or
   to the table owner, and local/dev setups habitually connect as the owner.
   The trigger closes that hole.

Revision ID: 0003_audit_append_only
Revises: 0002_initial_schema
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_audit_append_only"
down_revision: str | None = "0002_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_forbid_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_log is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_forbid_mutation();
        """
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_forbid_mutation()")
