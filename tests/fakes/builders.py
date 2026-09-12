"""Terse constructors for building `Fact` and `PriceBar` fixtures in tests.

Accepts ISO date strings and plain numbers so a test reads as data, not
boilerplate:

    fact(cik="0000320193", concept=CanonicalConcept.REVENUE, value=1000,
         period_end="2014-12-31", filed_date="2015-02-15")

`accession_number` defaults to a deterministic derivation of
`(cik, period_end, filed_date)` — never a uuid or an incrementing counter — so
tests that rely on the default are reproducible across runs. Tests that need to
control the tie-break in `get_latest_fact` (same `filed_date`, different
`accession_number`) pass `accession_number` explicitly.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik, Fact, PriceBar, Unit


def _as_date(value: dt.date | str) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


def _default_accession_number(cik: Cik, period_end: dt.date, filed_date: dt.date) -> str:
    return f"{cik.value}-{period_end.isoformat()}-{filed_date.isoformat()}"


def fact(
    *,
    cik: str | Cik,
    concept: CanonicalConcept,
    value: Decimal | int | str,
    period_end: dt.date | str,
    filed_date: dt.date | str,
    period_start: dt.date | str | None = None,
    unit: Unit = Unit.USD,
    raw_tag: str | None = None,
    fiscal_year: int | None = None,
    fiscal_period: str = "FY",
    form: str = "10-K",
    accession_number: str | None = None,
    is_derived: bool = False,
) -> Fact:
    """Build a `Fact` tersely, with sensible defaults for everything not central
    to the point-in-time behaviour under test.
    """
    resolved_cik = cik if isinstance(cik, Cik) else Cik.parse(cik)
    resolved_period_end = _as_date(period_end)
    resolved_filed_date = _as_date(filed_date)
    resolved_period_start = _as_date(period_start) if period_start is not None else None
    return Fact(
        cik=resolved_cik,
        concept=concept,
        raw_tag=raw_tag if raw_tag is not None else concept.value,
        unit=unit,
        value=Decimal(str(value)),
        period_start=resolved_period_start,
        period_end=resolved_period_end,
        fiscal_year=fiscal_year if fiscal_year is not None else resolved_period_end.year,
        fiscal_period=fiscal_period,
        form=form,
        filed_date=resolved_filed_date,
        accession_number=(
            accession_number
            if accession_number is not None
            else _default_accession_number(resolved_cik, resolved_period_end, resolved_filed_date)
        ),
        is_derived=is_derived,
    )


def bar(
    *,
    ticker: str,
    date: dt.date | str,
    close: Decimal | int | str,
    open: Decimal | int | str | None = None,  # shadows builtin; matches PriceBar's field name
    high: Decimal | int | str | None = None,
    low: Decimal | int | str | None = None,
    adj_close: Decimal | int | str | None = None,
    volume: int = 0,
) -> PriceBar:
    """Build a `PriceBar` tersely. Unset OHLC fields default to `close`."""
    resolved_close = Decimal(str(close))
    return PriceBar(
        ticker=ticker,
        date=_as_date(date),
        open=Decimal(str(open)) if open is not None else resolved_close,
        high=Decimal(str(high)) if high is not None else resolved_close,
        low=Decimal(str(low)) if low is not None else resolved_close,
        close=resolved_close,
        adj_close=Decimal(str(adj_close)) if adj_close is not None else resolved_close,
        volume=volume,
    )
