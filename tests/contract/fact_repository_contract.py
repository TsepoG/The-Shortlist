"""PHASE_0.md §6 scenarios for `FactRepository`, backend-independent.

Each function takes a `make_repo` factory shaped exactly like
`tests.fakes.guarded_fact_repo`: `(*facts: Fact) -> FactRepository`, already
wrapped by the real guard. A caller supplies facts and gets back a repository
ready to query — how those facts got there (an in-memory list, or a real INSERT
into Postgres) is the only thing that differs between callers.

Function names match the `test_*` names PHASE_0.md §6 enumerates, minus the
`test_` prefix, so a unit test and its integration counterpart are trivially
traceable to each other and to the spec.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from shortlist.data.repository import FactRepository
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes.builders import fact

FactRepoFactory = Callable[..., FactRepository]

CIK = Cik.parse("0000000001")
Q4_2014_PERIOD_END = dt.date(2014, 12, 31)


# --- §6.1 The core invariant --------------------------------------------------


def facts_filed_after_asof_are_excluded(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
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


def facts_filed_on_asof_are_included(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
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


def facts_filed_before_asof_are_included(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
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


# --- §6.2 Adversarial — TESTING.md §1.2 ---------------------------------------


def q4_2014_not_visible_before_filing(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
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


def q4_2014_visible_after_filing(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
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


# --- §6.3 Restatements ---------------------------------------------------------


def _repo_with_restatement(make_repo: FactRepoFactory) -> FactRepository:
    original = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=100,
        period_end=Q4_2014_PERIOD_END,
        filed_date="2015-02-20",
    )
    revision = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=90,
        period_end=Q4_2014_PERIOD_END,
        filed_date="2016-05-10",
    )
    return make_repo(original, revision)


def asof_before_restatement_returns_original(make_repo: FactRepoFactory) -> None:
    repo = _repo_with_restatement(make_repo)

    result = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-06-01"), Q4_2014_PERIOD_END
    )

    assert result is not None
    assert result.value == 100
    assert result.filed_date.isoformat() == "2015-02-20"


def asof_after_restatement_returns_revision(make_repo: FactRepoFactory) -> None:
    repo = _repo_with_restatement(make_repo)

    result = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2016-08-01"), Q4_2014_PERIOD_END
    )

    assert result is not None
    assert result.value == 90
    assert result.filed_date.isoformat() == "2016-05-10"


def both_revisions_retrievable(make_repo: FactRepoFactory) -> None:
    repo = _repo_with_restatement(make_repo)

    result = repo.get_facts(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2016-08-01"), period_end=Q4_2014_PERIOD_END
    )

    assert len(result) == 2
    values_by_filed_date = {f.filed_date.isoformat(): f.value for f in result}
    assert values_by_filed_date == {"2015-02-20": 100, "2016-05-10": 90}


# --- get_facts_for_universe ----------------------------------------------------


def get_facts_for_universe_filters_by_asof(make_repo: FactRepoFactory) -> None:
    other_cik = Cik.parse("0000000002")
    repo = make_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        ),
        fact(
            cik=other_cik,
            concept=CanonicalConcept.REVENUE,
            value=200,
            period_end="2014-12-31",
            filed_date="2015-06-01",
        ),
    )

    result = repo.get_facts_for_universe(
        [CIK, other_cik], [CanonicalConcept.REVENUE], AsOfDate.parse("2015-03-01")
    )

    assert len(result) == 1
    assert result[0].cik == CIK


# --- §6.6 Determinism -----------------------------------------------------------


def repeated_query_identical(make_repo: FactRepoFactory) -> None:
    repo = make_repo(
        fact(
            cik=CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000,
            period_end=Q4_2014_PERIOD_END,
            filed_date="2015-02-15",
        ),
        fact(
            cik=CIK,
            concept=CanonicalConcept.NET_INCOME,
            value=200,
            period_end=Q4_2014_PERIOD_END,
            filed_date="2015-02-15",
        ),
    )
    as_of = AsOfDate.parse("2015-03-01")

    first = repo.get_facts(CIK, CanonicalConcept.REVENUE, as_of)
    second = repo.get_facts(CIK, CanonicalConcept.REVENUE, as_of)

    assert first == second


def tie_break_is_deterministic(make_repo: FactRepoFactory) -> None:
    lower_accession = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=100,
        period_end=Q4_2014_PERIOD_END,
        filed_date="2015-02-20",
        accession_number="0000000001-15-000001",
    )
    higher_accession = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=999,
        period_end=Q4_2014_PERIOD_END,
        filed_date="2015-02-20",
        accession_number="0000000001-15-000002",
    )
    as_of = AsOfDate.parse("2015-03-01")

    repo_in_order = make_repo(lower_accession, higher_accession)
    result_in_order = repo_in_order.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, as_of, Q4_2014_PERIOD_END
    )

    repo_reversed = make_repo(higher_accession, lower_accession)
    result_reversed = repo_reversed.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, as_of, Q4_2014_PERIOD_END
    )

    assert result_in_order is not None
    assert result_reversed is not None
    assert result_in_order.value == 999
    assert result_reversed.value == 999
    assert result_in_order.accession_number == "0000000001-15-000002"
    assert result_reversed.accession_number == "0000000001-15-000002"
