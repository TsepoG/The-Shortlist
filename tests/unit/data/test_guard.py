"""PHASE_0.md §6.4 — the guard cannot be bypassed.

Even a repository that ignores `as_of` entirely must be caught by the guard on the
way out. This is the test that justifies the guard's existence: correctness of the
query is not trusted, only the guard's own check on the result.
"""

import datetime as dt

import pytest

from shortlist.data.factory import wrap_fact_repository, wrap_price_repository
from shortlist.data.guard import (
    GuardedFactRepository,
    GuardedPriceRepository,
    LookAheadError,
)
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from tests.fakes.builders import bar, fact
from tests.fakes.leaking_repository import LeakingFactRepository, LeakingPriceRepository
from tests.fakes.memory_repository import InMemoryFactRepository, InMemoryPriceRepository

CIK = Cik.parse("0000000001")


def test_guard_raises_on_leaking_repository_for_facts() -> None:
    leaking = LeakingFactRepository(
        [
            fact(
                cik=CIK,
                concept=CanonicalConcept.REVENUE,
                value=1000,
                period_end="2014-12-31",
                filed_date="2015-02-20",
            )
        ]
    )
    guarded = wrap_fact_repository(leaking)

    with pytest.raises(LookAheadError) as excinfo:
        guarded.get_facts(CIK, CanonicalConcept.REVENUE, AsOfDate.parse("2015-01-15"))

    message = str(excinfo.value)
    assert CIK.value in message
    assert CanonicalConcept.REVENUE.value in message
    assert "2015-02-20" in message
    assert "2015-01-15" in message


def test_guard_raises_on_leaking_repository_for_prices() -> None:
    leaking = LeakingPriceRepository([bar(ticker="AAPL", date="2015-02-20", close=100)])
    guarded = wrap_price_repository(leaking)

    with pytest.raises(LookAheadError) as excinfo:
        guarded.get_bars(
            "AAPL",
            start=dt.date(2015, 1, 1),
            end=dt.date(2015, 1, 15),
            as_of=AsOfDate.parse("2015-01-15"),
        )

    message = str(excinfo.value)
    assert "AAPL" in message
    assert "2015-02-20" in message
    assert "2015-01-15" in message


def test_factory_returns_guarded_instance() -> None:
    fact_repo = wrap_fact_repository(InMemoryFactRepository())
    price_repo = wrap_price_repository(InMemoryPriceRepository())

    assert isinstance(fact_repo, GuardedFactRepository)
    assert isinstance(price_repo, GuardedPriceRepository)


def test_guard_rejects_double_wrapping() -> None:
    already_guarded_facts = wrap_fact_repository(InMemoryFactRepository())
    already_guarded_prices = wrap_price_repository(InMemoryPriceRepository())

    with pytest.raises(TypeError):
        GuardedFactRepository(already_guarded_facts)

    with pytest.raises(TypeError):
        GuardedPriceRepository(already_guarded_prices)


def test_guard_rejects_untyped_as_of() -> None:
    guarded = wrap_fact_repository(InMemoryFactRepository())

    with pytest.raises(TypeError):
        guarded.get_facts(
            CIK,
            CanonicalConcept.REVENUE,
            dt.date(2015, 1, 15),  # type: ignore[arg-type]
        )


def test_get_facts_for_universe_filters_by_asof() -> None:
    other_cik = Cik.parse("0000000002")
    guarded = wrap_fact_repository(
        InMemoryFactRepository(
            [
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
            ]
        )
    )

    result = guarded.get_facts_for_universe(
        [CIK, other_cik], [CanonicalConcept.REVENUE], AsOfDate.parse("2015-03-01")
    )

    assert len(result) == 1
    assert result[0].cik == CIK


def test_get_bars_raises_when_end_exceeds_asof() -> None:
    # Q5: a caller asking for bars past its own as-of date is confused; the guard
    # rejects the request outright rather than silently clamping the window.
    guarded = wrap_price_repository(InMemoryPriceRepository())

    with pytest.raises(LookAheadError):
        guarded.get_bars(
            "AAPL",
            start=dt.date(2015, 1, 1),
            end=dt.date(2015, 2, 1),
            as_of=AsOfDate.parse("2015-01-15"),
        )
