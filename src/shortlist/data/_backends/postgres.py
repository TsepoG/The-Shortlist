"""The PostgreSQL-backed `FactRepository` implementation, and the append-only writer.

This module is private (`shortlist.data._backends`) and never re-exported from
`shortlist.data` — see `factory.py` for why a raw, unguarded repository is not
obtainable by normal application code. `PostgresFactRepository` implements the
phase 0 `FactRepository` protocol exactly; it is wrapped by the same
`GuardedFactRepository` the in-memory fake uses, so the point-in-time invariant is
enforced identically regardless of backend.

`PostgresFactWriter` is a separate class, deliberately outside every read
protocol: there is no write method on `FactRepository` to bypass, and the guard
has nothing to say about an honest write of a fact's true `filed_date`. See
`docs/phases/PHASE_1_NOTES.md` §9 (ported from the phase 1 plan) for the full
reasoning on why nothing here needs to go around the guard.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from shortlist.data._backends.schema import UNIQUE_CONSTRAINT_NAME, fundamental_facts
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, Unit

# Batch size for INSERT ... ON CONFLICT DO NOTHING. Large enough to make a
# multi-thousand-row backfill efficient, small enough to keep one statement's
# parameter count and memory footprint reasonable.
_INSERT_BATCH_SIZE = 1000


def _row_to_fact(row: sa.Row[Any]) -> Fact:
    value = row.value
    if not isinstance(value, Decimal):
        # psycopg maps NUMERIC -> Decimal natively; this is a hard assertion, not
        # a coercion, because a float here would silently violate CLAUDE.md's
        # "explicit Decimal for money and ratios ... never float equality".
        raise TypeError(f"Expected Decimal for fundamental_facts.value, got {type(value)}")
    return Fact(
        cik=Cik(row.cik),
        concept=CanonicalConcept(row.concept),
        raw_tag=row.raw_tag,
        unit=Unit(row.unit),
        value=value,
        period_start=row.period_start,
        period_end=row.period_end,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        form=row.form,
        filed_date=row.filed_date,
        accession_number=row.accession_number,
        is_derived=row.is_derived,
    )


def _fact_sort_key(fact: Fact) -> tuple[str, str, date, date, str]:
    # Matches tests/fakes/memory_repository.py exactly, per
    # docs/phases/PHASE_0_NOTES.md Q9: determinism is a property of the layer,
    # not of one backend, so both sort identically rather than each relying on
    # ORDER BY producing the same tie-break by coincidence.
    return (
        fact.cik.value,
        fact.concept.value,
        fact.period_end,
        fact.filed_date,
        fact.accession_number,
    )


class PostgresFactRepository:
    """Implements `FactRepository` (phase 0) against PostgreSQL.

    Accepts an `Engine` or a `Connection`: integration tests inject a
    transaction that gets rolled back at the end of each test, rather than
    truncating the table between cases.
    """

    def __init__(self, bind: Engine | Connection) -> None:
        self._bind = bind

    def _execute(self, statement: sa.Select[Any]) -> Sequence[sa.Row[Any]]:
        if isinstance(self._bind, Connection):
            return self._bind.execute(statement).all()
        with self._bind.connect() as conn:
            return conn.execute(statement).all()

    def get_facts(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> Sequence[Fact]:
        t = fundamental_facts
        stmt = sa.select(t).where(
            t.c.cik == cik.value,
            t.c.concept == concept.value,
            t.c.filed_date <= as_of.value,
        )
        # Exact-match filters, per docs/phases/PHASE_0_NOTES.md Q3 — not a range.
        if period_start is not None:
            stmt = stmt.where(t.c.period_start == period_start)
        if period_end is not None:
            stmt = stmt.where(t.c.period_end == period_end)
        facts = [_row_to_fact(row) for row in self._execute(stmt)]
        return tuple(sorted(facts, key=_fact_sort_key))

    def get_latest_fact(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_end: date,
    ) -> Fact | None:
        t = fundamental_facts
        stmt = (
            sa.select(t)
            .where(
                t.c.cik == cik.value,
                t.c.concept == concept.value,
                t.c.period_end == period_end,
                t.c.filed_date <= as_of.value,
            )
            # Ties on filed_date break by accession_number descending, per
            # PHASE_0.md §3 and docs/phases/PHASE_0_NOTES.md Q3.
            .order_by(t.c.filed_date.desc(), t.c.accession_number.desc())
            .limit(1)
        )
        rows = self._execute(stmt)
        return _row_to_fact(rows[0]) if rows else None

    def get_facts_for_universe(
        self,
        ciks: Sequence[Cik],
        concepts: Sequence[CanonicalConcept],
        as_of: AsOfDate,
    ) -> Sequence[Fact]:
        if not ciks or not concepts:
            return ()
        t = fundamental_facts
        stmt = sa.select(t).where(
            t.c.cik.in_([c.value for c in ciks]),
            t.c.concept.in_([c.value for c in concepts]),
            t.c.filed_date <= as_of.value,
        )
        facts = [_row_to_fact(row) for row in self._execute(stmt)]
        return tuple(sorted(facts, key=_fact_sort_key))


@dataclass(frozen=True, slots=True)
class InsertResult:
    """Outcome of a batch write, so idempotency is observable rather than assumed."""

    inserted: int
    skipped: int


def _fact_to_row(fact: Fact) -> dict[str, object]:
    return {
        "cik": fact.cik.value,
        "concept": fact.concept.value,
        "raw_tag": fact.raw_tag,
        "unit": fact.unit.value,
        "value": fact.value,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "form": fact.form,
        "filed_date": fact.filed_date,
        "accession_number": fact.accession_number,
        "is_derived": fact.is_derived,
    }


class PostgresFactWriter:
    """Append-only writer for `fundamental_facts`.

    Deliberately not part of `FactRepository` or any other read protocol — see
    the module docstring. Every insert is `ON CONFLICT DO NOTHING` against
    `UNIQUE_CONSTRAINT_NAME`, so re-running ingestion is safe by construction:
    a fact already stored is skipped, never overwritten, and the database's own
    append-only trigger (in the migration) rejects any path that tried to
    update or delete a row regardless.
    """

    def __init__(self, bind: Engine | Connection) -> None:
        self._bind = bind

    def insert_facts(self, facts: Sequence[Fact]) -> InsertResult:
        if not facts:
            return InsertResult(inserted=0, skipped=0)

        total_inserted = 0
        for start in range(0, len(facts), _INSERT_BATCH_SIZE):
            batch = facts[start : start + _INSERT_BATCH_SIZE]
            rows = [_fact_to_row(f) for f in batch]
            # `.returning(...)` and counting the rows actually returned, not
            # `result.rowcount`: confirmed by direct reproduction that
            # psycopg3 reports rowcount as -1 ("cannot be determined") for
            # this exact multi-row VALUES + ON CONFLICT DO NOTHING pattern,
            # unconditionally — including when some or all rows in the batch
            # are genuinely new. The write itself was always correct; only
            # that count was ever wrong. A conflicted row contributes no
            # RETURNING row, so this count is exact.
            stmt = (
                pg_insert(fundamental_facts)
                .values(rows)
                .on_conflict_do_nothing(constraint=UNIQUE_CONSTRAINT_NAME)
                .returning(fundamental_facts.c.id)
            )
            if isinstance(self._bind, Connection):
                result = self._bind.execute(stmt)
            else:
                with self._bind.begin() as conn:
                    result = conn.execute(stmt)
            total_inserted += len(result.fetchall())

        return InsertResult(inserted=total_inserted, skipped=len(facts) - total_inserted)
