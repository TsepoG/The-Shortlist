"""PHASE_1.md §4's fixture discovery script: ranks candidates, chooses nothing,
hardcodes no company.
"""

from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik
from shortlist.ingest.companyfacts import UnmappedTag
from shortlist.ingest.scripts.discover_fixtures import (
    filing_index_url,
    find_custom_tag_candidates,
    find_fiscal_year_change_candidates,
    find_restatement_candidates,
    find_smaller_reporting_company_candidates,
)
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")
OTHER_CIK = Cik.parse("0000000002")


def test_filing_index_url_strips_leading_zeros_and_dashes() -> None:
    url = filing_index_url(Cik.parse("0000320193"), "0000320193-15-000001")
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019315000001/"


def test_finds_material_restatement_candidate() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("5000000000"),
            period_end="2014-12-31",
            filed_date="2015-02-20",
            accession_number="acc-original",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("3000000000"),  # 2B off, well past tolerance
            period_end="2014-12-31",
            filed_date="2016-05-10",
            accession_number="acc-revised",
        ),
    ]

    candidates = find_restatement_candidates(facts)

    assert len(candidates) == 1
    assert candidates[0].cik == CIK
    assert candidates[0].earlier_accession == "acc-original"
    assert candidates[0].later_accession == "acc-revised"
    assert candidates[0].difference == Decimal("-2000000000")


def test_immaterial_difference_is_not_a_restatement_candidate() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("5000000000"),
            period_end="2014-12-31",
            filed_date="2015-02-20",
            accession_number="acc-a",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("5000000001"),  # $1 off
            period_end="2014-12-31",
            filed_date="2016-05-10",
            accession_number="acc-b",
        ),
    ]

    assert find_restatement_candidates(facts) == ()


def test_single_filing_is_not_a_restatement_candidate() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2014-12-31",
            filed_date="2015-02-20",
            accession_number="acc-a",
        )
    ]

    assert find_restatement_candidates(facts) == ()


def test_restatement_candidates_ranked_by_materiality() -> None:
    facts = [
        # Company 1: small difference (but still material)
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("2000000000"),
            period_end="2014-12-31",
            filed_date="2015-02-20",
            accession_number="a1",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("1950000000"),
            period_end="2014-12-31",
            filed_date="2016-05-10",
            accession_number="a2",
        ),
        # Company 2: huge difference
        fact(
            cik=OTHER_CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("9000000000"),
            period_end="2014-12-31",
            filed_date="2015-02-20",
            accession_number="b1",
        ),
        fact(
            cik=OTHER_CIK,
            concept=CanonicalConcept.REVENUE,
            value=Decimal("1000000000"),
            period_end="2014-12-31",
            filed_date="2016-05-10",
            accession_number="b2",
        ),
    ]

    candidates = find_restatement_candidates(facts)

    assert len(candidates) == 2
    assert candidates[0].cik == OTHER_CIK  # bigger difference ranked first


def test_finds_fiscal_year_change_candidate() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2013-12-31",
            filed_date="2014-02-20",
            fiscal_year=2013,
            fiscal_period="FY",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1100,
            period_end="2014-06-30",  # FY end month shifted
            filed_date="2014-09-20",
            fiscal_year=2014,
            fiscal_period="FY",
        ),
    ]

    candidates = find_fiscal_year_change_candidates(facts)

    assert len(candidates) == 1
    assert candidates[0].cik == CIK
    assert candidates[0].earlier_period_end.month == 12
    assert candidates[0].later_period_end.month == 6


def test_no_fiscal_year_change_when_month_is_stable() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2013-12-31",
            filed_date="2014-02-20",
            fiscal_year=2013,
            fiscal_period="FY",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1100,
            period_end="2014-12-31",
            filed_date="2015-02-20",
            fiscal_year=2014,
            fiscal_period="FY",
        ),
    ]

    assert find_fiscal_year_change_candidates(facts) == ()


def test_quarterly_facts_are_ignored_for_fiscal_year_change() -> None:
    facts = [
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2013-09-30",
            filed_date="2013-10-20",
            fiscal_year=2013,
            fiscal_period="Q3",
        ),
    ]

    assert find_fiscal_year_change_candidates(facts) == ()


def test_finds_smaller_reporting_company_by_below_median_coverage() -> None:
    concepts = [CanonicalConcept.REVENUE, CanonicalConcept.NET_INCOME, CanonicalConcept.CASH]
    facts = [
        # Full coverage, well-established company
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1,
            period_end="2012-12-31",
            filed_date="2013-02-01",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.NET_INCOME,
            value=1,
            period_end="2012-12-31",
            filed_date="2013-02-01",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.CASH,
            value=1,
            period_end="2012-12-31",
            filed_date="2013-02-01",
        ),
        # Thin coverage, later start
        fact(
            cik=OTHER_CIK,
            concept=CanonicalConcept.REVENUE,
            value=1,
            period_end="2012-12-31",
            filed_date="2013-06-01",
        ),
    ]

    candidates = find_smaller_reporting_company_candidates(facts, concepts)

    assert len(candidates) == 1
    assert candidates[0].cik == OTHER_CIK


def test_finds_custom_tag_candidate_for_revenue() -> None:
    unmapped = [
        UnmappedTag("acme", "SpecialRevenueRecognitionMetric", count=12),
        UnmappedTag("acme", "SomeUnrelatedDisclosure", count=50),
    ]

    candidates = find_custom_tag_candidates(unmapped)

    assert len(candidates) == 1
    assert candidates[0].tag == "SpecialRevenueRecognitionMetric"
    assert candidates[0].plausible_concept_hint == "revenue"


def test_custom_tag_candidates_ranked_by_frequency() -> None:
    unmapped = [
        UnmappedTag("acme", "RareRevenueThing", count=2),
        UnmappedTag("acme", "CommonSalesMetric", count=40),
    ]

    candidates = find_custom_tag_candidates(unmapped)

    assert candidates[0].tag == "CommonSalesMetric"
