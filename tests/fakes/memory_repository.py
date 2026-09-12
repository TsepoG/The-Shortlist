"""A list-backed, CORRECT implementation of the repository protocols.

Used by every phase 0 test (via `tests/fakes/__init__.py`, which wraps it in the
real guard) and by later unit tests that need facts without a database. Being
list-backed and O(n) per query is fine — phase 0 has no performance requirement,
only a correctness one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, PriceBar


def _fact_sort_key(fact: Fact) -> tuple[str, str, dt.date, dt.date, str]:
    return (
        fact.cik.value,
        fact.concept.value,
        fact.period_end,
        fact.filed_date,
        fact.accession_number,
    )


def _bar_sort_key(bar: PriceBar) -> tuple[str, dt.date]:
    return (bar.ticker, bar.date)


class InMemoryFactRepository:
    """Correctly filters every read by `filed_date <= as_of`."""

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
        matches = [
            f
            for f in self._facts
            if f.cik == cik
            and f.concept == concept
            and f.filed_date <= as_of.value
            and (period_start is None or f.period_start == period_start)
            and (period_end is None or f.period_end == period_end)
        ]
        return tuple(sorted(matches, key=_fact_sort_key))

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
            if f.cik == cik
            and f.concept == concept
            and f.period_end == period_end
            and f.filed_date <= as_of.value
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda f: (f.filed_date, f.accession_number))

    def get_facts_for_universe(
        self,
        ciks: Sequence[Cik],
        concepts: Sequence[CanonicalConcept],
        as_of: AsOfDate,
    ) -> Sequence[Fact]:
        cik_set = set(ciks)
        concept_set = set(concepts)
        matches = [
            f
            for f in self._facts
            if f.cik in cik_set and f.concept in concept_set and f.filed_date <= as_of.value
        ]
        return tuple(sorted(matches, key=_fact_sort_key))


class InMemoryPriceRepository:
    """Correctly filters every read by `date <= as_of`."""

    def __init__(self, bars: Sequence[PriceBar] = ()) -> None:
        self._bars: tuple[PriceBar, ...] = tuple(bars)

    def get_bars(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        as_of: AsOfDate,
    ) -> Sequence[PriceBar]:
        matches = [
            b
            for b in self._bars
            if b.ticker == ticker and start <= b.date <= end and b.date <= as_of.value
        ]
        return tuple(sorted(matches, key=_bar_sort_key))
