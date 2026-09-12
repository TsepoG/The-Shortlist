"""Deliberately broken repositories that ignore `as_of` entirely.

These exist solely to prove the guard catches a leak that a backend's own query
logic failed to prevent (`PHASE_0.md` §6.4). Nothing in production code, and no
other fixture, should ever import these — keeping them in their own module makes
that an explicit, visible choice at the import site rather than an accident.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, PriceBar


class LeakingFactRepository:
    """Returns every matching fact regardless of `filed_date` vs `as_of`."""

    def __init__(self, facts: Sequence[Fact] = ()) -> None:
        self._facts: tuple[Fact, ...] = tuple(facts)

    def get_facts(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
    ) -> Sequence[Fact]:
        return tuple(f for f in self._facts if f.cik == cik and f.concept == concept)

    def get_latest_fact(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_end: dt.date,
    ) -> Fact | None:
        candidates = [
            f
            for f in self._facts
            if f.cik == cik and f.concept == concept and f.period_end == period_end
        ]
        if not candidates:
            return None
        # Ignores as_of: happily returns the latest filing even if filed in the future.
        return max(candidates, key=lambda f: (f.filed_date, f.accession_number))

    def get_facts_for_universe(
        self,
        ciks: Sequence[Cik],
        concepts: Sequence[CanonicalConcept],
        as_of: AsOfDate,
    ) -> Sequence[Fact]:
        cik_set = set(ciks)
        concept_set = set(concepts)
        return tuple(f for f in self._facts if f.cik in cik_set and f.concept in concept_set)


class LeakingPriceRepository:
    """Returns every matching bar on or after `start`, ignoring `end` and `as_of`.

    Models a backend whose upper-bound query logic is wrong even when the caller's
    request is perfectly legitimate (`end == as_of`) — the scenario the guard's
    post-check on returned bars exists to catch, distinct from the guard's
    pre-check on a confused caller-supplied `end` (see `guard.py`).
    """

    def __init__(self, bars: Sequence[PriceBar] = ()) -> None:
        self._bars: tuple[PriceBar, ...] = tuple(bars)

    def get_bars(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        as_of: AsOfDate,
    ) -> Sequence[PriceBar]:
        return tuple(b for b in self._bars if b.ticker == ticker and b.date >= start)
