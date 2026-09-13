"""The `fundamental_facts` table definition — the single source of schema truth.

Both the Alembic migration (`alembic/versions/0001_create_fundamental_facts.py`)
and `postgres.py` (the reader and writer) import this module, so the schema is
defined exactly once. The migration creates this table via
`metadata.create_all(..., tables=[fundamental_facts])` rather than restating the
columns with `op.create_table`, so there is no second copy of the schema that
could drift from this one.

The append-only trigger (rejecting `UPDATE`/`DELETE`) has no SQLAlchemy Core
equivalent and is raw DDL in the migration itself — see `PHASE_1_NOTES.md` for
why that lives outside this module.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

FUNDAMENTAL_FACTS_TABLE_NAME = "fundamental_facts"

# Unique constraint per docs/phases/PHASE_1_NOTES.md's correction to PHASE_1.md §5:
# period_start is included (with NULLS NOT DISTINCT) so a single filing's FY and Q4
# rows for the same concept — same cik/concept/period_end/accession/unit, differing
# only in period_start — do not collide into one row. fiscal_period is deliberately
# excluded: it is redundant with period_start and including it would let an
# inconsistently-labeled filing (fp=FY here, fp=Q4 there for the same period) slip
# past the constraint as two rows instead of one.
UNIQUE_CONSTRAINT_NAME = "uq_fundamental_facts"
READ_INDEX_NAME = "ix_fundamental_facts_read"

fundamental_facts = sa.Table(
    FUNDAMENTAL_FACTS_TABLE_NAME,
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("cik", sa.CHAR(10), nullable=False),
    sa.Column("concept", sa.Text, nullable=False),
    sa.Column("raw_tag", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("value", sa.Numeric, nullable=False),
    sa.Column("period_start", sa.Date, nullable=True),
    sa.Column("period_end", sa.Date, nullable=False),
    sa.Column("fiscal_year", sa.Integer, nullable=False),
    sa.Column("fiscal_period", sa.Text, nullable=False),
    sa.Column("form", sa.Text, nullable=False),
    sa.Column("filed_date", sa.Date, nullable=False),
    sa.Column("accession_number", sa.Text, nullable=False),
    sa.Column("is_derived", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column(
        "ingested_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Index(
        READ_INDEX_NAME,
        "cik",
        "concept",
        "period_end",
        sa.text("filed_date DESC"),
    ),
    sa.UniqueConstraint(
        "cik",
        "concept",
        "period_start",
        "period_end",
        "accession_number",
        "unit",
        name=UNIQUE_CONSTRAINT_NAME,
        # NULLS NOT DISTINCT: PostgreSQL 15+ (DESIGN.md §6 requires 16+). Without
        # this, two balance-sheet-instant rows (period_start IS NULL) for the same
        # cik/concept/period_end/accession/unit would be treated as non-duplicate
        # by Postgres's default NULL handling, breaking idempotent re-ingestion for
        # every instant concept (total_assets, cash, stockholders_equity, ...).
        postgresql_nulls_not_distinct=True,
    ),
)
