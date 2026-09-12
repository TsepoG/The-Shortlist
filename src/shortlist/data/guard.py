"""The point-in-time enforcement wrapper.

This is the one place the `filed_date <= as_of` / `date <= as_of` invariant is
enforced as a hard runtime assertion, independent of whether the backend's own
query logic got it right. `PHASE_0.md` §4: "The guard duplicates what correct SQL
would already do. That redundancy is intentional: the query is where the bug will
be, so the assertion must live somewhere else."

The guard runs in production, not only under test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from shortlist.data.repository import FactRepository, PriceRepository
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, PriceBar


class LookAheadError(RuntimeError):
    """A repository returned (or was asked for) data from beyond its `as_of` cutoff.

    This is a bug signal, never something to catch and continue past. It always
    names the offending identifier, the concept (when applicable), the offending
    date, the `as_of` cutoff, and the method where the leak was caught.
    """

    def __init__(
        self,
        *,
        method: str,
        as_of: AsOfDate,
        offending_date: dt.date,
        identifier: str,
        concept: str | None = None,
    ) -> None:
        self.method = method
        self.as_of = as_of
        self.offending_date = offending_date
        self.identifier = identifier
        self.concept = concept
        concept_part = f" concept={concept}" if concept is not None else ""
        message = (
            f"LookAheadError in {method}: identifier={identifier}{concept_part} "
            f"date={offending_date.isoformat()} exceeds as_of={as_of.value.isoformat()}"
        )
        super().__init__(message)


def _require_as_of(as_of: AsOfDate, method: str) -> None:
    if not isinstance(as_of, AsOfDate):
        raise TypeError(
            f"{method} requires an AsOfDate, got {type(as_of).__name__}. "
            "There is no default 'now' — every callsite must pass one explicitly."
        )


def _check_facts(facts: Sequence[Fact], as_of: AsOfDate, method: str) -> None:
    for fact in facts:
        if fact.filed_date > as_of.value:
            raise LookAheadError(
                method=method,
                as_of=as_of,
                offending_date=fact.filed_date,
                identifier=str(fact.cik),
                concept=fact.concept.value,
            )


def _check_bars(bars: Sequence[PriceBar], as_of: AsOfDate, method: str) -> None:
    for bar in bars:
        if bar.date > as_of.value:
            raise LookAheadError(
                method=method,
                as_of=as_of,
                offending_date=bar.date,
                identifier=bar.ticker,
            )


class GuardedFactRepository:
    """Wraps a `FactRepository` and asserts `filed_date <= as_of` on every result.

    Constructing this directly with another `GuardedFactRepository` as the inner
    repository is rejected — the wrapping seam has exactly one meaning, and
    double-wrapping would either be redundant or (if someone unwrapped incorrectly)
    silently drop enforcement.
    """

    def __init__(self, inner: FactRepository) -> None:
        if isinstance(inner, GuardedFactRepository):
            raise TypeError(
                "Cannot wrap an already-guarded FactRepository. "
                "Pass the raw backend implementation instead."
            )
        self._inner = inner

    def get_facts(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
    ) -> Sequence[Fact]:
        _require_as_of(as_of, "get_facts")
        results = self._inner.get_facts(cik, concept, as_of, period_start, period_end)
        _check_facts(results, as_of, "get_facts")
        return results

    def get_latest_fact(
        self,
        cik: Cik,
        concept: CanonicalConcept,
        as_of: AsOfDate,
        period_end: dt.date,
    ) -> Fact | None:
        _require_as_of(as_of, "get_latest_fact")
        result = self._inner.get_latest_fact(cik, concept, as_of, period_end)
        if result is not None:
            _check_facts([result], as_of, "get_latest_fact")
        return result

    def get_facts_for_universe(
        self,
        ciks: Sequence[Cik],
        concepts: Sequence[CanonicalConcept],
        as_of: AsOfDate,
    ) -> Sequence[Fact]:
        _require_as_of(as_of, "get_facts_for_universe")
        results = self._inner.get_facts_for_universe(ciks, concepts, as_of)
        _check_facts(results, as_of, "get_facts_for_universe")
        return results


class GuardedPriceRepository:
    """Wraps a `PriceRepository` and asserts `date <= as_of` on every result.

    Also exposes `get_trailing_high`, which is not part of `PriceRepository` (see
    `repository.py` and `docs/phases/PHASE_0_NOTES.md` Q2): it is computed here from
    this guard's own already-guarded `get_bars`, so the trailing-high window ending
    at `as_of` is a structural property rather than something each backend must get
    right independently.
    """

    def __init__(self, inner: PriceRepository) -> None:
        if isinstance(inner, GuardedPriceRepository):
            raise TypeError(
                "Cannot wrap an already-guarded PriceRepository. "
                "Pass the raw backend implementation instead."
            )
        self._inner = inner

    def get_bars(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        as_of: AsOfDate,
    ) -> Sequence[PriceBar]:
        _require_as_of(as_of, "get_bars")
        if end > as_of.value:
            raise LookAheadError(
                method="get_bars",
                as_of=as_of,
                offending_date=end,
                identifier=ticker,
            )
        results = self._inner.get_bars(ticker, start, end, as_of)
        _check_bars(results, as_of, "get_bars")
        return results

    def get_trailing_high(
        self,
        ticker: str,
        as_of: AsOfDate,
        window_days: int,
    ) -> Decimal | None:
        """The maximum `adj_close` in the inclusive window ending at `as_of`.

        Derived from `get_bars`, so it goes through the same enforcement — a
        backend cannot leak a post-`as_of` high through this path even if it tries.
        """
        _require_as_of(as_of, "get_trailing_high")
        start = as_of.value - dt.timedelta(days=window_days)
        bars = self.get_bars(ticker, start, as_of.value, as_of)
        if not bars:
            return None
        return max(bar.adj_close for bar in bars)
