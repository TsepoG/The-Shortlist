"""create prices and corporate_actions

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13

Creates `prices` and `corporate_actions` per PHASE_2.md §2, with the
correction recorded in docs/phases/PHASE_2_NOTES.md §0.1: neither table
carries fundamental_facts's append-only trigger. An adjusted close is a
derived, recomputable value (DESIGN.md §3.2), not an as-filed historical
assertion, and the price provider itself retroactively re-adjusts its whole
series after every new split — so re-ingestion must be able to overwrite a
stale adjustment, not merely skip a duplicate. `corporate_actions` also gains
a unique constraint PHASE_2.md §2.2 did not specify, without which every
re-run would duplicate every action (docs/phases/PHASE_2_NOTES.md, "Ambiguous
/ underspecified" #3).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from shortlist.data._backends.schema import corporate_actions, metadata, prices

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Creates exactly the tables defined in schema.py — no restated column
    # list here, so there is only ever one definition of each schema.
    metadata.create_all(bind=op.get_bind(), tables=[prices, corporate_actions])


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), tables=[corporate_actions, prices])
