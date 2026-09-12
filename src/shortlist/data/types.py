"""Core point-in-time data types.

All types are immutable. `AsOfDate` is a distinct, runtime-checkable wrapper around
`datetime.date` rather than a plain alias — see `docs/phases/PHASE_0_NOTES.md` Q1. It
never defaults to "today": every caller must construct one explicitly from a concrete
date, so there is no code path where "as of" silently means "now".
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

_CIK_PATTERN = re.compile(r"^\d{10}$")


@dataclass(frozen=True, slots=True)
class AsOfDate:
    """A point-in-time cutoff. Distinct from `datetime.date` so an unwrapped date
    cannot be passed to a repository method by accident.

    There is deliberately no `.today()` constructor: the whole point of this type is
    that "as of" is always an explicit, named date, never an implicit "now".
    """

    value: dt.date

    def __post_init__(self) -> None:
        if not isinstance(self.value, dt.date) or isinstance(self.value, dt.datetime):
            raise TypeError(f"AsOfDate requires a datetime.date, got {type(self.value).__name__}")

    @classmethod
    def parse(cls, value: str) -> AsOfDate:
        """Construct from an ISO-8601 date string (e.g. '2015-03-01')."""
        return cls(dt.date.fromisoformat(value))


@dataclass(frozen=True, slots=True)
class Cik:
    """SEC EDGAR company identifier: a zero-padded 10-character digit string."""

    value: str

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.match(self.value):
            raise ValueError(f"Cik must be a zero-padded 10-digit string, got {self.value!r}")

    @classmethod
    def parse(cls, value: int | str) -> Cik:
        """Zero-pad an int or numeric string into a valid Cik."""
        digits = str(value)
        if not digits.isdigit():
            raise ValueError(f"Cik.parse requires digits, got {value!r}")
        if len(digits) > 10:
            raise ValueError(f"Cik.parse value too long for 10 digits: {value!r}")
        return cls(digits.zfill(10))

    def __str__(self) -> str:
        return self.value


class CanonicalConcept(StrEnum):
    """Normalized fundamental concepts, after alias resolution in phase 1.

    This is exactly the set of canonical concept names enumerated in
    `docs/phases/PHASE_1.md` §3 — see `docs/phases/PHASE_0_NOTES.md` Q6. Phase 0 owns
    only the names; the raw-tag-to-concept alias chains are phase 1's responsibility.
    """

    REVENUE = "revenue"
    COST_OF_REVENUE = "cost_of_revenue"
    GROSS_PROFIT = "gross_profit"
    OPERATING_INCOME = "operating_income"
    NET_INCOME = "net_income"
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    STOCKHOLDERS_EQUITY = "stockholders_equity"
    CURRENT_ASSETS = "current_assets"
    CURRENT_LIABILITIES = "current_liabilities"
    CASH = "cash"
    LONG_TERM_DEBT = "long_term_debt"
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPEX = "capex"
    SHARES_DILUTED = "shares_diluted"
    SHARES_OUTSTANDING = "shares_outstanding"


class Unit(StrEnum):
    """Measurement unit of a fact's value. Never mixed across concepts."""

    USD = "USD"
    SHARES = "shares"
    USD_PER_SHARE = "USD/shares"
    PURE = "pure"


@dataclass(frozen=True, slots=True)
class Fact:
    """One row of `fundamental_facts`: a single (company, concept, period, filing)
    observation. Append-only — restatements are new rows with a later `filed_date`,
    never mutations of an existing row.

    `is_derived` is True for a fact computed from other facts (e.g. `gross_profit`
    derived from `revenue - cost_of_revenue`) rather than tagged directly by the
    filer. See `docs/phases/PHASE_1.md` §3 and `src/shortlist/ingest/derive.py`.
    """

    cik: Cik
    concept: CanonicalConcept
    raw_tag: str
    unit: Unit
    value: Decimal
    period_start: dt.date | None
    period_end: dt.date
    fiscal_year: int
    fiscal_period: str
    form: str
    filed_date: dt.date
    accession_number: str
    is_derived: bool = False


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One day of adjusted OHLCV for a ticker."""

    ticker: str
    date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int
