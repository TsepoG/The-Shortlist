"""Derives the two canonical concepts PHASE_1.md §3 says to compute when a filer
didn't tag them directly: `gross_profit` and `total_liabilities`.

**Same-accession-only** for both, confirmed during phase 1 planning (see
`docs/phases/PHASE_1_NOTES.md`): derivation happens only when every input comes
from one filing and one period, so every provenance field on the derived row —
`filed_date`, `accession_number`, `form`, `fiscal_year`, `fiscal_period` —
inherits unambiguously from that single filing. Inputs spanning two filings
produce no derived fact at all: a `raw_tag` that could only name one of the two
filings its number actually came from is worse than an absent value, and §3's
whole reason for keeping `raw_tag` is that a disputed number must be traceable
to exactly one place.

Neither is ever called when the filer tagged the concept explicitly — as-filed
always wins. That check lives in the caller (`companyfacts.py`), not here:
these functions only know how to combine inputs, not whether a direct tagging
already answered the question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Fact, Unit

DERIVED_RAW_TAG = "derived:revenue-cost_of_revenue"
DERIVED_TOTAL_LIABILITIES_RAW_TAG = "derived:liabilities_and_equity-stockholders_equity"

# The raw tag `derive_total_liabilities` consumes as its input. It is **not** an
# alias for `total_liabilities` and is deliberately absent from
# `concepts.ALIAS_CHAINS`: `LiabilitiesAndStockholdersEquity` is the balance
# sheet's grand total (it equals `Assets`), not a liabilities figure. Mapping it
# as an alias would store assets under the liabilities concept.
LIABILITIES_AND_EQUITY_TAG = ("us-gaap", "LiabilitiesAndStockholdersEquity")

# Tags whose nonzero presence means `LiabilitiesAndStockholdersEquity -
# stockholders_equity` is NOT liabilities: the balance sheet has an equity-side
# bucket that is neither, so the remainder would silently absorb it.
#
# This is a **refusal rule, not an alias list** — nothing here is mapped,
# normalized, or stored, and no canonical concept is invented for it
# (CLAUDE.md's "do not invent"). Its only effect is to decline to derive.
# Found against real filings: Xilinx tags
# `TemporaryEquityCarryingAmountAttributableToParent` (FY2014 would have been
# overstated by $34,999,000 / 1.5%, its Q1-Q3 by ~$360M / 17.8%) and AMD tags
# `MinorityInterest` at 2009-12-26 ($1,076,000,000 / 12.8%). Both tag
# parent-only `StockholdersEquity` and nothing more inclusive, so the alias
# chain cannot see the difference. `RedeemableNoncontrollingInterest*` is
# included on the same reasoning though no company in phase 1's scope uses it.
_OTHER_EQUITY_TAG_PREFIXES = ("TemporaryEquity", "RedeemableNoncontrollingInterest")
_OTHER_EQUITY_TAGS = ("MinorityInterest",)

# Every raw tag this module reads as a derivation input, for the unmapped-tag
# report: these are genuinely unclaimed by any alias chain, but they are
# consumed rather than ignored, so PHASE_1.md §6's report can say so instead of
# presenting them as untriaged missing aliases.
DERIVATION_INPUT_TAGS: frozenset[tuple[str, str]] = frozenset({LIABILITIES_AND_EQUITY_TAG})


def is_other_equity_component_tag(namespace: str, tag: str) -> bool:
    """True for a tag that sits on the equity side of the balance sheet but is
    neither `total_liabilities` nor `stockholders_equity` — see
    `_OTHER_EQUITY_TAG_PREFIXES`. Used only to refuse a derivation.
    """
    if namespace != LIABILITIES_AND_EQUITY_TAG[0]:
        return False
    return tag.startswith(_OTHER_EQUITY_TAG_PREFIXES) or tag in _OTHER_EQUITY_TAGS


@dataclass(frozen=True, slots=True)
class BalanceSheetTotal:
    """`us-gaap:LiabilitiesAndStockholdersEquity` for one (period, accession),
    plus the sum of any other equity-side buckets that same filing reports for
    that same slot.

    Not a `Fact`, deliberately: `LiabilitiesAndStockholdersEquity` is not one of
    phase 1's 16 canonical concepts and must never be stored as one. This type
    exists only to carry a derivation input from the parser to
    `derive_total_liabilities` without it ever becoming a row.
    """

    value: Decimal
    period_start: date | None
    period_end: date
    accession_number: str
    other_equity_components: Decimal = Decimal(0)


def derive_gross_profit(revenue: Fact, cost_of_revenue: Fact) -> Fact | None:
    """`revenue - cost_of_revenue`, marked `is_derived`, or `None` if the two
    facts don't share one accession and one period.
    """
    if revenue.concept is not CanonicalConcept.REVENUE:
        raise ValueError(f"expected a revenue Fact, got concept={revenue.concept}")
    if cost_of_revenue.concept is not CanonicalConcept.COST_OF_REVENUE:
        raise ValueError(f"expected a cost_of_revenue Fact, got concept={cost_of_revenue.concept}")

    if revenue.cik != cost_of_revenue.cik:
        raise ValueError("revenue and cost_of_revenue must be for the same company")

    if revenue.accession_number != cost_of_revenue.accession_number:
        return None
    if revenue.period_start != cost_of_revenue.period_start:
        return None
    if revenue.period_end != cost_of_revenue.period_end:
        return None

    # Both concepts expect USD (see concepts.py); a mismatch here means one of
    # the inputs was already invalid and should never have reached this point.
    if revenue.unit is not Unit.USD or cost_of_revenue.unit is not Unit.USD:
        raise ValueError("derive_gross_profit requires USD inputs on both sides")

    return Fact(
        cik=revenue.cik,
        concept=CanonicalConcept.GROSS_PROFIT,
        raw_tag=DERIVED_RAW_TAG,
        unit=Unit.USD,
        value=revenue.value - cost_of_revenue.value,
        period_start=revenue.period_start,
        period_end=revenue.period_end,
        fiscal_year=revenue.fiscal_year,
        fiscal_period=revenue.fiscal_period,
        form=revenue.form,
        filed_date=revenue.filed_date,
        accession_number=revenue.accession_number,
        is_derived=True,
    )


def derive_total_liabilities(total: BalanceSheetTotal, stockholders_equity: Fact) -> Fact | None:
    """`total.value - stockholders_equity.value`, marked `is_derived`, or `None`
    if the two don't share one accession and one period, or if `total` carries
    a nonzero other-equity-component balance the alias chain can't distinguish
    from liabilities (see `_OTHER_EQUITY_TAG_PREFIXES` / `_OTHER_EQUITY_TAGS`).

    The `None` case is a deliberate refusal, not a missing-data gap: storing a
    number known to be off by double-digit percentages (confirmed against real
    Xilinx and AMD filings — see module docstring) would be worse than leaving
    the period uncovered.
    """
    if stockholders_equity.concept is not CanonicalConcept.STOCKHOLDERS_EQUITY:
        raise ValueError(
            f"expected a stockholders_equity Fact, got concept={stockholders_equity.concept}"
        )

    if total.accession_number != stockholders_equity.accession_number:
        return None
    if total.period_start != stockholders_equity.period_start:
        return None
    if total.period_end != stockholders_equity.period_end:
        return None

    if stockholders_equity.unit is not Unit.USD:
        raise ValueError("derive_total_liabilities requires a USD stockholders_equity input")

    if total.other_equity_components != Decimal(0):
        return None

    return Fact(
        cik=stockholders_equity.cik,
        concept=CanonicalConcept.TOTAL_LIABILITIES,
        raw_tag=DERIVED_TOTAL_LIABILITIES_RAW_TAG,
        unit=Unit.USD,
        value=total.value - stockholders_equity.value,
        period_start=stockholders_equity.period_start,
        period_end=stockholders_equity.period_end,
        fiscal_year=stockholders_equity.fiscal_year,
        fiscal_period=stockholders_equity.fiscal_period,
        form=stockholders_equity.form,
        filed_date=stockholders_equity.filed_date,
        accession_number=stockholders_equity.accession_number,
        is_derived=True,
    )
