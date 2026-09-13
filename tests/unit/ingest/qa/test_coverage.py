"""PHASE_1.md §6 / TESTING.md §1.5 coverage monitoring."""

from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik, Fact
from shortlist.ingest.qa.coverage import cells_below_threshold, compute_coverage
from tests.fakes.builders import fact

CIK_A = Cik.parse("0000000001")
CIK_B = Cik.parse("0000000002")
CIK_C = Cik.parse("0000000003")
CIK_D = Cik.parse("0000000004")


def _annual_fact(cik: Cik, concept: CanonicalConcept, fiscal_year: int) -> Fact:
    return fact(
        cik=cik,
        concept=concept,
        value=100,
        period_end=f"{fiscal_year}-12-31",
        filed_date=f"{fiscal_year + 1}-02-15",
        fiscal_year=fiscal_year,
        fiscal_period="FY",
    )


def test_full_coverage_is_one() -> None:
    universe = [CIK_A, CIK_B]
    facts = [
        _annual_fact(CIK_A, CanonicalConcept.REVENUE, 2014),
        _annual_fact(CIK_B, CanonicalConcept.REVENUE, 2014),
    ]

    cells = compute_coverage(universe, [CanonicalConcept.REVENUE], facts)

    assert len(cells) == 1
    assert cells[0].fraction == Decimal("1")
    assert cells[0].below_threshold is False


def test_partial_coverage_below_threshold_is_flagged() -> None:
    universe = [CIK_A, CIK_B, CIK_C, CIK_D]
    facts = [_annual_fact(CIK_A, CanonicalConcept.REVENUE, 2010)]  # only 1 of 4

    cells = compute_coverage(universe, [CanonicalConcept.REVENUE], facts)

    assert len(cells) == 1
    assert cells[0].fraction == Decimal("0.25")
    assert cells[0].below_threshold is True
    assert cells_below_threshold(cells) == cells


def test_coverage_computed_independently_per_fiscal_year() -> None:
    universe = [CIK_A, CIK_B]
    facts = [
        _annual_fact(CIK_A, CanonicalConcept.REVENUE, 2014),
        _annual_fact(CIK_B, CanonicalConcept.REVENUE, 2014),
        _annual_fact(CIK_A, CanonicalConcept.REVENUE, 2015),  # only A in 2015
    ]

    cells = compute_coverage(universe, [CanonicalConcept.REVENUE], facts)

    by_year = {c.fiscal_year: c.fraction for c in cells}
    assert by_year[2014] == Decimal("1")
    assert by_year[2015] == Decimal("0.5")


def test_quarterly_facts_are_excluded_from_coverage() -> None:
    universe = [CIK_A]
    facts = [
        fact(
            cik=CIK_A,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date="2015-02-15",
            fiscal_year=2014,
            fiscal_period="Q4",
        )
    ]

    cells = compute_coverage(universe, [CanonicalConcept.REVENUE], facts)

    assert cells == ()


def test_coverage_computed_independently_per_concept() -> None:
    universe = [CIK_A, CIK_B]
    facts = [
        _annual_fact(CIK_A, CanonicalConcept.REVENUE, 2014),
        _annual_fact(CIK_B, CanonicalConcept.REVENUE, 2014),
        _annual_fact(CIK_A, CanonicalConcept.NET_INCOME, 2014),  # only A
    ]

    cells = compute_coverage(
        universe, [CanonicalConcept.REVENUE, CanonicalConcept.NET_INCOME], facts
    )

    by_concept = {c.concept: c.fraction for c in cells}
    assert by_concept[CanonicalConcept.REVENUE] == Decimal("1")
    assert by_concept[CanonicalConcept.NET_INCOME] == Decimal("0.5")
