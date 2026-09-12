"""PHASE_0.md §6.6 — determinism.

The scoring engine (phase 4) depends on identical as-of inputs producing
identical output; that guarantee starts here; a nondeterministic data layer would
make it unattainable no matter how carefully scoring is written.
"""

import datetime as dt

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")
PERIOD_END = dt.date(2014, 12, 31)


def test_repeated_query_identical() -> None:
    repo = guarded_fact_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.NET_INCOME,
            value=200,
            period_end=PERIOD_END,
            filed_date="2015-02-15",
        ),
    )
    as_of = AsOfDate.parse("2015-03-01")

    first = repo.get_facts(CIK, CanonicalConcept.REVENUE, as_of)
    second = repo.get_facts(CIK, CanonicalConcept.REVENUE, as_of)

    assert first == second


def test_tie_break_is_deterministic() -> None:
    # Two facts filed on the exact same date; only accession_number distinguishes
    # which is "latest". The result must not depend on insertion order.
    lower_accession = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=100,
        period_end=PERIOD_END,
        filed_date="2015-02-20",
        accession_number="0000000001-15-000001",
    )
    higher_accession = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=999,
        period_end=PERIOD_END,
        filed_date="2015-02-20",
        accession_number="0000000001-15-000002",
    )
    as_of = AsOfDate.parse("2015-03-01")

    repo_in_order = guarded_fact_repo(lower_accession, higher_accession)
    repo_reversed = guarded_fact_repo(higher_accession, lower_accession)

    result_in_order = repo_in_order.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, as_of, PERIOD_END
    )
    result_reversed = repo_reversed.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, as_of, PERIOD_END
    )

    assert result_in_order is not None
    assert result_reversed is not None
    assert result_in_order.value == 999
    assert result_reversed.value == 999
    assert result_in_order.accession_number == "0000000001-15-000002"
    assert result_reversed.accession_number == "0000000001-15-000002"
