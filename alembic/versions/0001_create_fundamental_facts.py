"""create fundamental_facts

Revision ID: 0001
Revises:
Create Date: 2026-09-12

Creates the append-only `fundamental_facts` table per PHASE_1.md §5, with the
unique-constraint correction recorded in docs/phases/PHASE_1_NOTES.md (period_start
included, NULLS NOT DISTINCT) and a database-level trigger that rejects UPDATE and
DELETE, making CLAUDE.md invariant #4 ("Append-only facts") structural rather than
a convention — the same reasoning as the point-in-time read guard in
src/shortlist/data/guard.py: the query (or, here, the write) is where a bug would
be, so the assertion lives somewhere else too.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from shortlist.data._backends.schema import fundamental_facts, metadata

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER_FUNCTION_NAME = "fundamental_facts_reject_mutation"
_TRIGGER_NAME = "fundamental_facts_append_only"


def upgrade() -> None:
    # Creates exactly the table defined in schema.py — no restated column list
    # here, so there is only ever one definition of this schema to keep in sync.
    metadata.create_all(bind=op.get_bind(), tables=[fundamental_facts])

    op.execute(f"""
        CREATE FUNCTION {_TRIGGER_FUNCTION_NAME}() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'fundamental_facts is append-only: % is not permitted (row id=%)',
                TG_OP,
                COALESCE(OLD.id, NEW.id);
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute(f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE UPDATE OR DELETE ON fundamental_facts
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION_NAME}();
    """)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON fundamental_facts;")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION_NAME}();")
    metadata.drop_all(bind=op.get_bind(), tables=[fundamental_facts])
