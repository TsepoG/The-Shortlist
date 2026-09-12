"""Derives `gross_profit = revenue - cost_of_revenue` when a filer didn't tag it
directly, per PHASE_1.md §3's parenthetical: "(derive as revenue -
cost_of_revenue when absent; mark derived facts distinctly)".

**Same-accession-only**, confirmed during phase 1 planning (see
`docs/phases/PHASE_1_NOTES.md`): derivation happens only when `revenue` and
`cost_of_revenue` come from one filing and one period, so every provenance field
on the derived row — `filed_date`, `accession_number`, `form`, `fiscal_year`,
`fiscal_period` — inherits unambiguously from that single filing. Inputs
spanning two filings produce no derived fact at all: a `raw_tag` that could only
name one of the two filings its number actually came from is worse than an
absent value, and §3's whole reason for keeping `raw_tag` is that a disputed
number must be traceable to exactly one place.

Never called when a filer tagged `GrossProfit` explicitly — as-filed always
wins. That check lives in the caller (`companyfacts.py`), not here: this
function only knows how to combine two facts, not whether a third already
answered the question.
"""

from __future__ import annotations

from shortlist.data.types import CanonicalConcept, Fact, Unit

DERIVED_RAW_TAG = "derived:revenue-cost_of_revenue"


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
