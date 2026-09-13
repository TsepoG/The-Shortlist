"""Repository protocols: the only interface anything above the data layer may use.

Every method takes an explicit `as_of: AsOfDate` with no default — there is no
overload that means "now". Nothing above this layer may query a raw source
directly; that is what makes the point-in-time invariant enforceable in exactly one
place (the guard in `guard.py`).

`get_trailing_high` is deliberately absent from `PriceRepository`. `PHASE_0.md` §3
declares it as a backend method returning a bare `Decimal | None`, but a bare
`Decimal` carries no date for the guard to check — see `docs/phases/PHASE_0_NOTES.md`
Q2. Instead the guard derives the trailing high itself from its own guarded
`get_bars`, so the point-in-time property is structural rather than trusted per
backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, PriceBar


@runtime_checkable
class FactRepository(Protocol):
    """Read access to `fundamental_facts`, scoped to a point-in-time cutoff."""

    def get_facts(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> Sequence[Fact]:
        """All facts for `cik`/`concept` with `filed_date <= as_of`.

        `period_start`/`period_end`, when given, are exact matches against the
        fact's own period — not range bounds. See `docs/phases/PHASE_0_NOTES.md` Q3.
        """
        ...

    def get_latest_fact(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_end: date,
    ) -> Fact | None:
        """The as-reported fact for `period_end`: among facts with that exact
        `period_end` and `filed_date <= as_of`, the one with the greatest
        `filed_date`. Ties on `filed_date` break by `accession_number` descending,
        so the result is deterministic.
        """
        ...

    def get_facts_for_universe(
        self,
        ciks: Sequence[Cik],
        concepts: Sequence[CanonicalConcept],
        as_of: AsOfDate,
    ) -> Sequence[Fact]:
        """All facts across `ciks` x `concepts` with `filed_date <= as_of`."""
        ...


@runtime_checkable
class PriceRepository(Protocol):
    """Read access to daily adjusted OHLCV bars, scoped to a point-in-time cutoff."""

    def get_bars(
        self,
        ticker: str,
        start: date,
        end: date,
        as_of: AsOfDate,
    ) -> Sequence[PriceBar]:
        """Bars for `ticker` in `[start, end]` with `bar.date <= as_of`."""
        ...


@runtime_checkable
class PriceReader(PriceRepository, Protocol):
    """The public read contract for prices, as exposed by the factory.

    Extends `PriceRepository` (what a raw backend implements) with
    `get_trailing_high`, which is not a backend method — the guard computes it
    from `get_bars` (`docs/phases/PHASE_0_NOTES.md` Q2). Callers obtained from
    `shortlist.data.factory` depend on this protocol, not on `PriceRepository`
    directly, so they can call `get_trailing_high` without a cast.
    """

    def get_trailing_high(
        self,
        ticker: str,
        as_of: AsOfDate,
        window_days: int,
    ) -> Decimal | None:
        """Max `adj_high` in the inclusive window ending at `as_of`.

        Intraday basis, decided in phase 2 against real data
        (`docs/phases/PHASE_2_NOTES.md` §4); phase 0 originally used
        `adj_close` because `adj_high` didn't exist yet — see `PHASE_0.md`
        §6.5 for the as-built decision.
        """
        ...
