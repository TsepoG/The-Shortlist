"""PHASE_0.md §6.1 — the core invariant, in its simplest form."""

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")


def test_facts_filed_after_asof_are_excluded() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        )
    )

    result = repo.get_facts(CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-01-15"))

    assert result == ()


def test_facts_filed_on_asof_are_included() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        )
    )

    result = repo.get_facts(CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-02-15"))

    assert len(result) == 1
    assert result[0].filed_date.isoformat() == "2015-02-15"


def test_facts_filed_before_asof_are_included() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        )
    )

    result = repo.get_facts(CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-03-01"))

    assert len(result) == 1
    assert result[0].value == 1000
