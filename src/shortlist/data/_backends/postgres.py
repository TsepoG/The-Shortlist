"""The PostgreSQL-backed `FactRepository`/`PriceRepository` implementations,
and their writers.

This module is private (`shortlist.data._backends`) and never re-exported from
`shortlist.data` — see `factory.py` for why a raw, unguarded repository is not
obtainable by normal application code. `PostgresFactRepository` and
`PostgresPriceRepository` implement phase 0's protocols exactly; both are
wrapped by the same guards the in-memory fakes use, so the point-in-time
invariant is enforced identically regardless of backend.

`PostgresFactWriter` and `PostgresPriceWriter` are separate classes,
deliberately outside every read protocol: there is no write method on
`FactRepository`/`PriceRepository` to bypass, and the guard has nothing to say
about an honest write. See `docs/phases/PHASE_1_NOTES.md` §9 (facts) and
`docs/phases/PHASE_2_NOTES.md` §0.1 (prices — including why prices, unlike
facts, is upserted rather than append-only) for the full reasoning.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.dml import ReturningInsert

from shortlist.data._backends.schema import (
    CORPORATE_ACTIONS_UNIQUE_CONSTRAINT_NAME,
    PRICES_UNIQUE_CONSTRAINT_NAME,
    UNIQUE_CONSTRAINT_NAME,
    corporate_actions,
    fundamental_facts,
    prices,
)
from shortlist.data.guard import AmbiguousTickerError
from shortlist.data.types import (
    AsOfDate,
    CanonicalConcept,
    Cik,
    CorporateActionRow,
    Fact,
    PriceBar,
    PriceRow,
    Unit,
)

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


# --- prices -------------------------------------------------------------


def _execute_select(bind: Engine | Connection, statement: sa.Select[Any]) -> Sequence[sa.Row[Any]]:
    if isinstance(bind, Connection):
        return bind.execute(statement).all()
    with bind.connect() as conn:
        return conn.execute(statement).all()


def _bar_sort_key(bar: PriceBar) -> tuple[str, date]:
    # Matches tests/fakes/memory_repository.py, per docs/phases/PHASE_0_NOTES.md
    # Q9: determinism is a property of the layer, not of one backend.
    return (bar.ticker, bar.date)


def _row_to_bar(row: sa.Row[Any]) -> PriceBar:
    """`prices` row -> `PriceBar`.

    `open`/`high`/`low`/`close` are nullable in storage (a genuine trading gap
    is real), but `PriceBar` — phase 0's read type — declares every OHLC field
    non-optional. The writer is responsible for never leaving one of these
    NULL for a row it writes (`_fill_missing_ohlc`, below); a NULL reaching
    here means that guarantee was violated somewhere, which is a bug to raise
    on, not paper over with an invented value at read time.
    """
    for field_name in ("open", "high", "low", "close", "adj_close", "adj_high"):
        value = getattr(row, field_name)
        if value is not None and not isinstance(value, Decimal):
            raise TypeError(f"Expected Decimal for prices.{field_name}, got {type(value)}")
    if row.open is None or row.high is None or row.low is None or row.close is None:
        raise TypeError(
            f"prices row for ticker={row.ticker!r} date={row.date} has a NULL "
            "OHLC field; the writer must fill these before insert."
        )
    if row.adj_high is None:
        # adj_high is nullable in storage (added in phase 2 — see schema.py),
        # but PriceBar declares it non-optional, same reasoning as the OHLC
        # fields above: the loader must always compute it
        # (loader.py's _approximate_adj_high), so a NULL reaching a read means
        # that guarantee was violated — including for any row written before
        # phase 2's §0.2 decision, which must be re-ingested, not read as-is.
        raise TypeError(
            f"prices row for ticker={row.ticker!r} date={row.date} has a NULL "
            "adj_high; re-run the price backfill to populate it."
        )
    return PriceBar(
        ticker=row.ticker,
        date=row.date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        adj_close=row.adj_close,
        adj_high=row.adj_high,
        volume=row.volume if row.volume is not None else 0,
    )


class PostgresPriceRepository:
    """Implements `PriceRepository` (phase 0) against PostgreSQL.

    Accepts an `Engine` or a `Connection`, exactly like `PostgresFactRepository`
    — see that class's docstring.
    """

    def __init__(self, bind: Engine | Connection) -> None:
        self._bind = bind

    def get_bars(
        self,
        ticker: str,
        start: date,
        end: date,
        as_of: AsOfDate,
    ) -> Sequence[PriceBar]:
        t = prices
        stmt = sa.select(t).where(
            t.c.ticker == ticker,
            t.c.date >= start,
            t.c.date <= end,
            t.c.date <= as_of.value,
        )
        rows = _execute_select(self._bind, stmt)

        # PHASE_2.md §0.1: keeping this protocol ticker-keyed relies on
        # ingestion-time attribution being correct. This is the check that
        # makes a failure of that attribution loud instead of silently
        # blending two companies' prices into one series — see
        # AmbiguousTickerError's docstring.
        distinct_ciks = {str(row.cik) for row in rows}
        if len(distinct_ciks) > 1:
            raise AmbiguousTickerError(ticker=ticker, ciks=sorted(distinct_ciks))

        bars = [_row_to_bar(row) for row in rows]
        return tuple(sorted(bars, key=_bar_sort_key))


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Outcome of an upsert batch: distinct from `InsertResult` because an
    upsert has no "skipped" case — every row is written, either as a fresh
    insert or as an update to an existing one. `updated` is what makes the
    idempotency proof concrete: re-running against unchanged source data
    should show `inserted=0` and `updated=len(rows)`, not merely "no error".
    """

    inserted: int
    updated: int


def _fill_missing_ohlc(row: PriceRow) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """`(open, high, low, close)`, each falling back to `adj_close` if the
    provider reported it as missing.

    Documented, not silent: a genuine provider gap (a halted session) must
    still produce a row `_row_to_bar` can turn back into a valid `PriceBar`,
    whose OHLC fields are non-optional. Falling back to `adj_close` — the one
    field this whole system actually reads for the dip screen — is the
    smallest assumption that keeps the row usable without inventing a
    plausible-looking but fabricated open/high/low/close.
    """
    fallback = row.adj_close
    return (
        row.open if row.open is not None else fallback,
        row.high if row.high is not None else fallback,
        row.low if row.low is not None else fallback,
        row.close if row.close is not None else fallback,
    )


def _price_row_to_dict(row: PriceRow) -> dict[str, object]:
    open_, high, low, close = _fill_missing_ohlc(row)
    return {
        "cik": row.cik.value,
        "ticker": row.ticker,
        "date": row.date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": row.volume if row.volume is not None else 0,
        "adj_close": row.adj_close,
        # Same fallback-to-adj_close reasoning as _fill_missing_ohlc, and the
        # same contract _row_to_bar enforces on read: this writer must never
        # leave adj_high NULL, since PriceBar (phase 0's read type) declares
        # it non-optional.
        "adj_high": row.adj_high if row.adj_high is not None else row.adj_close,
        "split_factor_at_ingest": row.split_factor_at_ingest,
        "source": row.source,
    }


def _action_row_to_dict(row: CorporateActionRow) -> dict[str, object]:
    return {
        "cik": row.cik.value,
        "ticker": row.ticker,
        "event_date": row.event_date,
        "event_type": row.event_type,
        "ratio_or_amount": row.ratio_or_amount,
        "source": row.source,
    }


class PostgresPriceWriter:
    """Upserting writer for `prices` and `corporate_actions`.

    Deliberately not part of `PriceRepository` or any other read protocol —
    see the module docstring. Unlike `PostgresFactWriter`, this writer upserts
    rather than skipping on conflict: `docs/phases/PHASE_2_NOTES.md` §0.1
    explains why prices are treated as a derived, recomputable value rather
    than as-filed truth, and why that makes overwriting the correct behaviour
    rather than a violation of CLAUDE.md invariant #4 (which is scoped to
    `fundamental_facts` only).
    """

    def __init__(self, bind: Engine | Connection) -> None:
        self._bind = bind

    def upsert_bars(self, rows: Sequence[PriceRow]) -> UpsertResult:
        if not rows:
            return UpsertResult(inserted=0, updated=0)

        total_inserted = 0
        total_updated = 0
        for start in range(0, len(rows), _INSERT_BATCH_SIZE):
            batch = rows[start : start + _INSERT_BATCH_SIZE]
            values = [_price_row_to_dict(r) for r in batch]
            insert_stmt: PgInsert = pg_insert(prices).values(values)
            stmt: ReturningInsert[tuple[Any]] = insert_stmt.on_conflict_do_update(
                constraint=PRICES_UNIQUE_CONSTRAINT_NAME,
                set_={
                    "ticker": insert_stmt.excluded.ticker,
                    "open": insert_stmt.excluded.open,
                    "high": insert_stmt.excluded.high,
                    "low": insert_stmt.excluded.low,
                    "close": insert_stmt.excluded.close,
                    "volume": insert_stmt.excluded.volume,
                    "adj_close": insert_stmt.excluded.adj_close,
                    "adj_high": insert_stmt.excluded.adj_high,
                    "split_factor_at_ingest": insert_stmt.excluded.split_factor_at_ingest,
                    "ingested_at": sa.func.now(),
                },
            ).returning(
                # `xmax = 0` is the standard Postgres idiom for "this row was
                # just inserted, not updated" within an ON CONFLICT DO UPDATE
                # RETURNING clause — the alternative to counting via rowcount,
                # which docs/phases/PHASE_1_NOTES.md already found unreliable
                # for a batched multi-row upsert under psycopg3.
                sa.literal_column("(xmax = 0)").label("was_insert")
            )
            if isinstance(self._bind, Connection):
                result = self._bind.execute(stmt)
            else:
                with self._bind.begin() as conn:
                    result = conn.execute(stmt)
            outcomes = [row.was_insert for row in result.fetchall()]
            total_inserted += sum(1 for was_insert in outcomes if was_insert)
            total_updated += sum(1 for was_insert in outcomes if not was_insert)

        return UpsertResult(inserted=total_inserted, updated=total_updated)

    def upsert_actions(self, rows: Sequence[CorporateActionRow]) -> UpsertResult:
        if not rows:
            return UpsertResult(inserted=0, updated=0)

        total_inserted = 0
        total_updated = 0
        for start in range(0, len(rows), _INSERT_BATCH_SIZE):
            batch = rows[start : start + _INSERT_BATCH_SIZE]
            values = [_action_row_to_dict(r) for r in batch]
            insert_stmt: PgInsert = pg_insert(corporate_actions).values(values)
            stmt: ReturningInsert[tuple[Any]] = insert_stmt.on_conflict_do_update(
                constraint=CORPORATE_ACTIONS_UNIQUE_CONSTRAINT_NAME,
                set_={
                    "ticker": insert_stmt.excluded.ticker,
                    "ratio_or_amount": insert_stmt.excluded.ratio_or_amount,
                    "ingested_at": sa.func.now(),
                },
            ).returning(sa.literal_column("(xmax = 0)").label("was_insert"))
            if isinstance(self._bind, Connection):
                result = self._bind.execute(stmt)
            else:
                with self._bind.begin() as conn:
                    result = conn.execute(stmt)
            outcomes = [row.was_insert for row in result.fetchall()]
            total_inserted += sum(1 for was_insert in outcomes if was_insert)
            total_updated += sum(1 for was_insert in outcomes if not was_insert)

        return UpsertResult(inserted=total_inserted, updated=total_updated)
