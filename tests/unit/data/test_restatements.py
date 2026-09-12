"""PHASE_0.md §6.3 — restatements.

Append-only facts give as-reported semantics for free: querying before a
restatement's filed_date returns the original figure; querying after returns the
revision. Both rows remain retrievable and distinguishable by filed_date.
"""

import datetime as dt

from shortlist.data.repository import FactRepository
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

CIK = Cik.parse("0000000001")
PERIOD_END = dt.date(2014, 12, 31)


def _repo_with_restatement() -> FactRepository:
    original = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=100,
        period_end=PERIOD_END,
        filed_date="2015-02-20",
    )
    revision = fact(
        cik=CIK,
        concept=CanonicalConcept.REVENUE,
        value=90,
        period_end=PERIOD_END,
        filed_date="2016-05-10",
    )
    return guarded_fact_repo(original, revision)


def test_asof_before_restatement_returns_original() -> None:
    repo = _repo_with_restatement()

    result = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-06-01"), PERIOD_END
    )

    assert result is not None
    assert result.value == 100
    assert result.filed_date.isoformat() == "2015-02-20"


def test_asof_after_restatement_returns_revision() -> None:
    repo = _repo_with_restatement()

    result = repo.get_latest_fact(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2016-08-01"), PERIOD_END
    )

    assert result is not None
    assert result.value == 90
    assert result.filed_date.isoformat() == "2016-05-10"


def test_both_revisions_retrievable() -> None:
    repo = _repo_with_restatement()

    result = repo.get_facts(
        CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2016-08-01"), period_end=PERIOD_END
    )

    assert len(result) == 2
    values_by_filed_date = {f.filed_date.isoformat(): f.value for f in result}
    assert values_by_filed_date == {"2015-02-20": 100, "2016-05-10": 90}
