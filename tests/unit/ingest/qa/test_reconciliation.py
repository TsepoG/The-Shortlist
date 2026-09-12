"""PHASE_1.md §6 / TESTING.md §1.4 reconciliation checks."""

from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik, Fact
from shortlist.ingest.qa.reconciliation import reconcile
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")
PERIOD_END = "2014-12-31"


def _f(concept: CanonicalConcept, value: Decimal | int) -> Fact:
    return fact(
        cik=CIK,
        concept=concept,
        value=value,
        period_end=PERIOD_END,
        filed_date="2015-02-15",
    )


def test_balance_sheet_identity_holds_produces_no_violation() -> None:
    facts = [
        _f(CanonicalConcept.TOTAL_ASSETS, 1000),
        _f(CanonicalConcept.TOTAL_LIABILITIES, 600),
        _f(CanonicalConcept.STOCKHOLDERS_EQUITY, 400),
    ]

    assert reconcile(facts) == ()


def test_balance_sheet_identity_violation_is_flagged() -> None:
    # Values large enough that the gap clears both the relative (0.5%) and
    # absolute ($1M) tolerance floors — see PHASE_1.md §6.
    facts = [
        _f(CanonicalConcept.TOTAL_ASSETS, Decimal("5000000000")),
        _f(CanonicalConcept.TOTAL_LIABILITIES, Decimal("3000000000")),
        _f(CanonicalConcept.STOCKHOLDERS_EQUITY, Decimal("1000000000")),  # sums to 4B, not 5B
    ]

    violations = reconcile(facts)

    assert len(violations) == 1
    assert violations[0].check == "assets_equal_liabilities_plus_equity"
    assert violations[0].cik == CIK


def test_balance_sheet_check_skipped_when_a_concept_is_missing() -> None:
    facts = [_f(CanonicalConcept.TOTAL_ASSETS, 1000)]  # no liabilities/equity

    assert reconcile(facts) == ()


def test_gross_margin_identity_holds_produces_no_violation() -> None:
    facts = [
        _f(CanonicalConcept.REVENUE, 1000),
        _f(CanonicalConcept.COST_OF_REVENUE, 400),
        _f(CanonicalConcept.GROSS_PROFIT, 600),
    ]

    assert reconcile(facts) == ()


def test_gross_margin_identity_violation_is_flagged() -> None:
    facts = [
        _f(CanonicalConcept.REVENUE, Decimal("5000000000")),
        _f(CanonicalConcept.COST_OF_REVENUE, Decimal("2000000000")),
        _f(CanonicalConcept.GROSS_PROFIT, Decimal("2500000000")),  # should be 3B
    ]

    violations = reconcile(facts)

    assert len(violations) == 1
    assert violations[0].check == "revenue_minus_cost_of_revenue_equals_gross_profit"


def test_within_relative_tolerance_is_not_flagged() -> None:
    # 0.5% of 1,000,000,000 is 5,000,000, well above the $1M floor.
    facts = [
        _f(CanonicalConcept.TOTAL_ASSETS, Decimal("1000004000")),  # 4,000,000 off
        _f(CanonicalConcept.TOTAL_LIABILITIES, Decimal("600000000")),
        _f(CanonicalConcept.STOCKHOLDERS_EQUITY, Decimal("400000000")),
    ]

    assert reconcile(facts) == ()


def test_outside_absolute_tolerance_on_small_values_is_flagged() -> None:
    # Small dollar values: 0.5% is tiny, so the $1M absolute floor is what
    # would normally protect against noise — a genuine $2M gap on a $10,000
    # balance sheet is still a real violation.
    facts = [
        _f(CanonicalConcept.TOTAL_ASSETS, Decimal("2000010000")),
        _f(CanonicalConcept.TOTAL_LIABILITIES, Decimal("5000")),
        _f(CanonicalConcept.STOCKHOLDERS_EQUITY, Decimal("5000")),
    ]

    violations = reconcile(facts)
    assert len(violations) == 1


def test_fy_and_q4_figures_sharing_a_period_end_are_not_mixed() -> None:
    # A single 10-K reports both the full-year and the Q4 figure for the same
    # concept, with the same period_end but different period_start (FY:
    # 2014-01-01, Q4: 2014-10-01). Grouping by period_end alone would let the
    # Q4 revenue collide with the FY cost_of_revenue below and either mask a
    # real violation or fabricate a fake one.
    fy_revenue = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=Decimal("4000000000"),
        period_start="2014-01-01",
        period_end=PERIOD_END,
        filed_date="2015-02-15",
    )
    fy_cost = fact(
        cik=CIK,
        concept=CanonicalConcept.COST_OF_REVENUE,
        value=Decimal("1500000000"),
        period_start="2014-01-01",
        period_end=PERIOD_END,
        filed_date="2015-02-15",
    )
    fy_gross_profit = fact(
        cik=CIK,
        concept=CanonicalConcept.GROSS_PROFIT,
        value=Decimal("2500000000"),  # 4B - 1.5B: correct for the FY period
        period_start="2014-01-01",
        period_end=PERIOD_END,
        filed_date="2015-02-15",
    )
    # Q4-only revenue, same period_end, different period_start — deliberately
    # NOT consistent with fy_cost/fy_gross_profit if ever mixed with them.
    q4_revenue = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=Decimal("1100000000"),
        period_start="2014-10-01",
        period_end=PERIOD_END,
        filed_date="2015-02-15",
    )

    violations = reconcile([fy_revenue, fy_cost, fy_gross_profit, q4_revenue])

    assert violations == ()


def test_restatement_of_one_concept_alone_does_not_create_a_false_violation() -> None:
    # Regression test for a real bug found running the phase 1 backfill: a
    # LATER filing restates only stockholders_equity for an older comparative
    # period, without re-reporting assets/liabilities for that same period.
    # Collapsing each concept to its own independently-latest fact would then
    # pair the restated equity against the OLDER filing's assets/liabilities —
    # two figures no single document ever asserted together. The fix: each
    # check picks the latest ACCESSION that reports every needed concept
    # TOGETHER, skipping an accession that reports only a subset.
    original_filing = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.TOTAL_ASSETS,
            value=Decimal("5000000000"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
            accession_number="original",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.TOTAL_LIABILITIES,
            value=Decimal("3000000000"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
            accession_number="original",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.STOCKHOLDERS_EQUITY,
            value=Decimal("2000000000"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
            accession_number="original",
        ),
    ]
    # A much later filing restates ONLY equity for this same comparative
    # period, to a value that would NOT balance against the original
    # assets/liabilities if naively combined with them.
    later_partial_restatement = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.STOCKHOLDERS_EQUITY,
            value=Decimal("2500000000"),
            period_end=PERIOD_END,
            filed_date="2018-08-03",
            accession_number="later-restatement",
        ),
    ]

    violations = reconcile(original_filing + later_partial_restatement)

    assert violations == ()


def test_violations_grouped_independently_per_company_and_period() -> None:
    other_cik = Cik.parse("0000000002")
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.TOTAL_ASSETS,
            value=1000,
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.TOTAL_LIABILITIES,
            value=600,
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.STOCKHOLDERS_EQUITY,
            value=400,
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        # Second company, violating:
        fact(
            cik=other_cik,
            concept=CanonicalConcept.TOTAL_ASSETS,
            value=Decimal("5000000000"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=other_cik,
            concept=CanonicalConcept.TOTAL_LIABILITIES,
            value=Decimal("1"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=other_cik,
            concept=CanonicalConcept.STOCKHOLDERS_EQUITY,
            value=Decimal("1"),
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
    ]

    violations = reconcile(facts)

    assert len(violations) == 1
    assert violations[0].cik == other_cik
