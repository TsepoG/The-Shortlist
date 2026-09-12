"""PHASE_1.md §3's gross_profit derivation and PHASE_1.md §7's explicit test
requirement: "Derived facts are marked is_derived."

Confirmed rule (docs/phases/PHASE_1_NOTES.md): same-accession-only. No derived
fact is ever produced across two filings.
"""

from decimal import Decimal

import pytest

from shortlist.data.types import CanonicalConcept, Fact, Unit
from shortlist.ingest.derive import DERIVED_RAW_TAG, derive_gross_profit
from tests.fakes.builders import fact

CIK = "0000000001"


def _revenue(**overrides: object) -> Fact:
    defaults: dict[str, object] = dict(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=1000,
        period_start="2014-01-01",
        period_end="2014-12-31",
        filed_date="2015-02-15",
        accession_number="acc-1",
    )
    defaults.update(overrides)
    return fact(**defaults)  # type: ignore[arg-type]


def _cost_of_revenue(**overrides: object) -> Fact:
    defaults: dict[str, object] = dict(
        cik=CIK,
        concept=CanonicalConcept.COST_OF_REVENUE,
        value=400,
        period_start="2014-01-01",
        period_end="2014-12-31",
        filed_date="2015-02-15",
        accession_number="acc-1",
    )
    defaults.update(overrides)
    return fact(**defaults)  # type: ignore[arg-type]


def test_derives_gross_profit_from_same_accession_inputs() -> None:
    revenue = _revenue()
    cost_of_revenue = _cost_of_revenue()

    result = derive_gross_profit(revenue, cost_of_revenue)

    assert result is not None
    assert result.value == Decimal("600")
    assert result.is_derived is True
    assert result.raw_tag == DERIVED_RAW_TAG


def test_derived_fact_inherits_provenance_from_the_shared_filing() -> None:
    revenue = _revenue(filed_date="2015-02-20", accession_number="acc-42", fiscal_year=2014)
    cost_of_revenue = _cost_of_revenue(
        filed_date="2015-02-20", accession_number="acc-42", fiscal_year=2014
    )

    result = derive_gross_profit(revenue, cost_of_revenue)

    assert result is not None
    assert result.filed_date == revenue.filed_date
    assert result.accession_number == "acc-42"
    assert result.fiscal_year == 2014
    assert result.fiscal_period == revenue.fiscal_period
    assert result.form == revenue.form


def test_no_derivation_across_two_accessions() -> None:
    revenue = _revenue(accession_number="acc-1")
    cost_of_revenue = _cost_of_revenue(accession_number="acc-2")

    result = derive_gross_profit(revenue, cost_of_revenue)

    assert result is None


def test_no_derivation_across_two_period_ends() -> None:
    revenue = _revenue(period_end="2014-12-31")
    cost_of_revenue = _cost_of_revenue(period_end="2015-12-31")

    result = derive_gross_profit(revenue, cost_of_revenue)

    assert result is None


def test_no_derivation_across_two_period_starts() -> None:
    # Same accession, same period_end (e.g. both describe FY2014 ending
    # 2014-12-31) but a different period_start — an FY figure and a Q4 figure
    # from the same filing must not be combined into one gross_profit.
    revenue = _revenue(period_start="2014-01-01")
    cost_of_revenue = _cost_of_revenue(period_start="2014-10-01")

    result = derive_gross_profit(revenue, cost_of_revenue)

    assert result is None


def test_rejects_non_usd_unit() -> None:
    revenue = _revenue(unit=Unit.PURE)
    cost_of_revenue = _cost_of_revenue()

    with pytest.raises(ValueError, match="requires USD"):
        derive_gross_profit(revenue, cost_of_revenue)


def test_rejects_wrong_concept_for_revenue_argument() -> None:
    not_revenue = _cost_of_revenue()
    cost_of_revenue = _cost_of_revenue()

    with pytest.raises(ValueError, match="expected a revenue Fact"):
        derive_gross_profit(not_revenue, cost_of_revenue)


def test_rejects_wrong_concept_for_cost_of_revenue_argument() -> None:
    revenue = _revenue()
    not_cost_of_revenue = _revenue()

    with pytest.raises(ValueError, match="expected a cost_of_revenue Fact"):
        derive_gross_profit(revenue, not_cost_of_revenue)


def test_rejects_mismatched_companies() -> None:
    revenue = _revenue(cik="0000000001")
    cost_of_revenue = _cost_of_revenue(cik="0000000002")

    with pytest.raises(ValueError, match="same company"):
        derive_gross_profit(revenue, cost_of_revenue)
