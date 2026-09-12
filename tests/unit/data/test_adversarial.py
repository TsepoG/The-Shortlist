"""PHASE_0.md §6.2 — the exact adversarial scenario from TESTING.md §1.2.

A Q4-2014 fact filed 2015-02-20. Queried before the filing date, it must not
exist at all — not a null, not the eventually-filed number, an empty result.
"""

import datetime as dt

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")
Q4_2014_PERIOD_END = dt.date(2014, 12, 31)


def test_q4_2014_not_visible_before_filing() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=5_000_000,
            period_end=Q4_2014_PERIOD_END,
            filed_date="2015-02-20",
        )
    )

    result = repo.get_facts(
        CIK,
        CanonicalConcept.REVENUE,
        AsOfDate.parse("2015-01-15"),
        period_end=Q4_2014_PERIOD_END,
    )

    assert result == ()

    latest = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-01-15"), Q4_2014_PERIOD_END
    )
    assert latest is None


def test_q4_2014_visible_after_filing() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=5_000_000,
            period_end=Q4_2014_PERIOD_END,
            filed_date="2015-02-20",
        )
    )

    result = repo.get_facts(
        CIK,
        CanonicalConcept.REVENUE,
        AsOfDate.parse("2015-03-01"),
        period_end=Q4_2014_PERIOD_END,
    )

    assert len(result) == 1
    assert result[0].value == 5_000_000

    latest = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-03-01"), Q4_2014_PERIOD_END
    )
    assert latest is not None
    assert latest.value == 5_000_000
