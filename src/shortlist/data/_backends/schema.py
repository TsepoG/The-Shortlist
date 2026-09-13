"""Table definitions — the single source of schema truth for every table.

Both the Alembic migrations (`alembic/versions/`) and `postgres.py` (the
readers and writers) import this module, so each schema is defined exactly
once. Each migration creates its tables via
`metadata.create_all(..., tables=[...])` rather than restating the columns with
`op.create_table`, so there is no second copy of any schema that could drift
from this one.

The `fundamental_facts` append-only trigger (rejecting `UPDATE`/`DELETE`) has no
SQLAlchemy Core equivalent and is raw DDL in its migration itself — see
`PHASE_1_NOTES.md` for why that lives outside this module. `prices` and
`corporate_actions` carry no such trigger — see `PHASE_2_NOTES.md` §0.1 for why
those two are upsertable by design, not an oversight.
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

# --- prices ------------------------------------------------------------------
#
# Unlike fundamental_facts, prices is upsertable, not append-only — see
# PHASE_2_NOTES.md §0.1: an adjusted close is a derived, recomputable value
# (DESIGN.md §3.2), not an as-filed historical assertion, and the price
# provider itself retroactively re-adjusts its whole series after every new
# split. Re-ingestion is therefore INSERT ... ON CONFLICT (cik, date, source)
# DO UPDATE, keyed so a re-run always converges the stored row to the
# provider's current view rather than accumulating stale, differently-adjusted
# duplicates.

PRICES_TABLE_NAME = "prices"
PRICES_UNIQUE_CONSTRAINT_NAME = "uq_prices"
PRICES_READ_INDEX_NAME = "ix_prices_read"

prices = sa.Table(
    PRICES_TABLE_NAME,
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("cik", sa.CHAR(10), nullable=False),
    # ticker is deliberately NOT part of the unique key (see below) — it is
    # the label a CIK traded under at ingest time, for audit/display and for
    # re-querying the provider; every join elsewhere in the system goes
    # through cik. PHASE_2_NOTES.md §0.1.
    sa.Column("ticker", sa.Text, nullable=False),
    sa.Column("date", sa.Date, nullable=False),
    sa.Column("open", sa.Numeric, nullable=True),
    sa.Column("high", sa.Numeric, nullable=True),
    sa.Column("low", sa.Numeric, nullable=True),
    sa.Column("close", sa.Numeric, nullable=True),
    sa.Column("volume", sa.BigInteger, nullable=True),
    sa.Column("adj_close", sa.Numeric, nullable=False),
    # Nullable, and populated from the start rather than added later: whether
    # PriceBar (phase 0's type) grows an adj_high field is the §0.2 decision,
    # deliberately deferred until jump-detection evidence exists — but that
    # evidence cannot be computed without ingested data, so the column is
    # created now. Storing a column is not the same as deciding the basis;
    # PriceBar and the guard's get_trailing_high are untouched until §0.2 is
    # resolved. See PHASE_2_NOTES.md §0.2.
    sa.Column("adj_high", sa.Numeric, nullable=True),
    # The cumulative split factor in force when this row was written. Not
    # used to compute adj_close (the provider already adjusts it — see
    # prices/provider.py), but recorded so a stale-adjustment-basis mix
    # across an upsert boundary is detectable after the fact rather than
    # only preventable in theory.
    sa.Column("split_factor_at_ingest", sa.Numeric, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column(
        "ingested_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Index(PRICES_READ_INDEX_NAME, "cik", "date"),
    sa.UniqueConstraint(
        "cik",
        "date",
        "source",
        name=PRICES_UNIQUE_CONSTRAINT_NAME,
    ),
)

# --- corporate_actions ---------------------------------------------------
#
# PHASE_2.md §2.2 specifies no unique constraint; without one, re-ingestion
# would duplicate every action on every run. (cik, event_date, event_type,
# source) mirrors the reasoning: same company, same day, same kind of event,
# same provider is one row — a genuine same-day split-and-dividend is two
# rows because event_type differs, which is correct (they are different
# facts, not a collision).

CORPORATE_ACTIONS_TABLE_NAME = "corporate_actions"
CORPORATE_ACTIONS_UNIQUE_CONSTRAINT_NAME = "uq_corporate_actions"
CORPORATE_ACTIONS_READ_INDEX_NAME = "ix_corporate_actions_read"

corporate_actions = sa.Table(
    CORPORATE_ACTIONS_TABLE_NAME,
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("cik", sa.CHAR(10), nullable=False),
    sa.Column("ticker", sa.Text, nullable=False),
    sa.Column("event_date", sa.Date, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),  # 'split' | 'dividend'
    sa.Column("ratio_or_amount", sa.Numeric, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column(
        "ingested_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Index(CORPORATE_ACTIONS_READ_INDEX_NAME, "cik", "event_date"),
    sa.UniqueConstraint(
        "cik",
        "event_date",
        "event_type",
        "source",
        name=CORPORATE_ACTIONS_UNIQUE_CONSTRAINT_NAME,
    ),
)
