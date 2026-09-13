"""PHASE_1.md §3's gross_profit and total_liabilities derivations, and
PHASE_1.md §7's explicit test requirement: "Derived facts are marked
is_derived."

Confirmed rule (docs/phases/PHASE_1_NOTES.md): same-accession-only. No derived
fact is ever produced across two filings.
"""

from datetime import date
from decimal import Decimal

import pytest

from shortlist.data.types import CanonicalConcept, Fact, Unit
from shortlist.ingest.derive import (
    DERIVED_RAW_TAG,
    DERIVED_TOTAL_LIABILITIES_RAW_TAG,
    BalanceSheetTotal,
    derive_gross_profit,
    derive_total_liabilities,
    is_other_equity_component_tag,
)
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


# --- derive_total_liabilities ------------------------------------------------


def _equity(**overrides: object) -> Fact:
    defaults: dict[str, object] = dict(
        cik=CIK,
        concept=CanonicalConcept.STOCKHOLDERS_EQUITY,
        value=300,
        period_end="2014-12-31",
        filed_date="2015-02-15",
        accession_number="acc-1",
    )
    defaults.update(overrides)
    return fact(**defaults)  # type: ignore[arg-type]


def _total(**overrides: object) -> BalanceSheetTotal:
    defaults: dict[str, object] = dict(
        value=Decimal("1000"),
        period_start=None,
        period_end=date(2014, 12, 31),
        accession_number="acc-1",
    )
    defaults.update(overrides)
    return BalanceSheetTotal(**defaults)  # type: ignore[arg-type]


def test_derives_total_liabilities_from_same_accession_inputs() -> None:
    total = _total(value=Decimal("1000"))
    equity = _equity(value=300)

    result = derive_total_liabilities(total, equity)

    assert result is not None
    assert result.value == Decimal("700")
    assert result.is_derived is True
    assert result.raw_tag == DERIVED_TOTAL_LIABILITIES_RAW_TAG
    assert result.concept is CanonicalConcept.TOTAL_LIABILITIES


def test_total_liabilities_inherits_provenance_from_the_equity_fact() -> None:
    equity = _equity(filed_date="2015-02-20", accession_number="acc-42", fiscal_year=2014)
    total = _total(accession_number="acc-42", period_end=date(2014, 12, 31))

    result = derive_total_liabilities(total, equity)

    assert result is not None
    assert result.filed_date == equity.filed_date
    assert result.accession_number == "acc-42"
    assert result.fiscal_year == 2014
    assert result.fiscal_period == equity.fiscal_period
    assert result.form == equity.form


def test_no_total_liabilities_derivation_across_two_accessions() -> None:
    total = _total(accession_number="acc-1")
    equity = _equity(accession_number="acc-2")

    assert derive_total_liabilities(total, equity) is None


def test_no_total_liabilities_derivation_across_two_period_ends() -> None:
    total = _total(period_end=date(2014, 12, 31))
    equity = _equity(period_end="2015-12-31")

    assert derive_total_liabilities(total, equity) is None


def test_no_total_liabilities_derivation_across_two_period_starts() -> None:
    total = _total(period_start=date(2014, 1, 1), period_end=date(2014, 12, 31))
    equity = _equity(period_start="2014-10-01", period_end="2014-12-31")

    assert derive_total_liabilities(total, equity) is None


def test_refuses_to_derive_when_other_equity_components_present() -> None:
    # Confirmed against real Xilinx/AMD filings: a nonzero temporary-equity or
    # minority-interest balance means LSE - stockholders_equity is NOT
    # liabilities. See derive.py's module docstring for the measured error.
    total = _total(other_equity_components=Decimal("1076000000"))
    equity = _equity()

    assert derive_total_liabilities(total, equity) is None


def test_derives_when_other_equity_components_is_exactly_zero() -> None:
    total = _total(other_equity_components=Decimal("0"))
    equity = _equity()

    assert derive_total_liabilities(total, equity) is not None


def test_rejects_wrong_concept_for_equity_argument() -> None:
    total = _total()
    not_equity = _revenue()

    with pytest.raises(ValueError, match="expected a stockholders_equity Fact"):
        derive_total_liabilities(total, not_equity)


def test_rejects_non_usd_equity_unit() -> None:
    total = _total()
    equity = _equity(unit=Unit.PURE)

    with pytest.raises(ValueError, match="requires a USD stockholders_equity"):
        derive_total_liabilities(total, equity)


def test_is_other_equity_component_tag_matches_known_buckets() -> None:
    assert is_other_equity_component_tag("us-gaap", "MinorityInterest") is True
    assert (
        is_other_equity_component_tag(
            "us-gaap", "TemporaryEquityCarryingAmountAttributableToParent"
        )
        is True
    )
    assert (
        is_other_equity_component_tag(
            "us-gaap", "RedeemableNoncontrollingInterestEquityCarryingAmount"
        )
        is True
    )


def test_is_other_equity_component_tag_rejects_unrelated_tags() -> None:
    assert is_other_equity_component_tag("us-gaap", "StockholdersEquity") is False
    assert is_other_equity_component_tag("us-gaap", "Liabilities") is False
    assert is_other_equity_component_tag("acme", "MinorityInterest") is False
