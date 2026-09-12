"""PHASE_0.md §6.5 — prices.

`get_trailing_high` is computed by the guard itself from its own guarded
`get_bars` (`docs/phases/PHASE_0_NOTES.md` Q2), so there are two properties to
prove separately: a correct backend's post-`as_of` bars do not affect the trailing
high (`test_trailing_high_window_ends_at_asof`), and a backend that gets this
wrong is caught rather than silently producing a wrong high
(`test_trailing_high_guard_catches_backend_that_leaks_a_future_bar`) — the guard's
job is to raise on a leak, never to quietly filter one out.
"""

import datetime as dt

import pytest

from shortlist.data.factory import wrap_price_repository
from shortlist.data.guard import LookAheadError
from shortlist.data.types import AsOfDate
from tests.fakes import guarded_price_repo
from tests.fakes.builders import bar
from tests.fakes.leaking_repository import LeakingPriceRepository

TICKER = "AAPL"


def test_price_bars_after_asof_excluded() -> None:
    repo = guarded_price_repo(
        bar(ticker=TICKER, date="2015-01-10", close=100),
        bar(ticker=TICKER, date="2015-01-20", close=200),
    )

    result = repo.get_bars(
        TICKER,
        start=dt.date(2015, 1, 1),
        end=dt.date(2015, 1, 15),
        as_of=AsOfDate.parse("2015-01-15"),
    )

    assert len(result) == 1
    assert result[0].date.isoformat() == "2015-01-10"


def test_trailing_high_window_ends_at_asof() -> None:
    # A higher price exists after as_of; a correct backend already excludes it
    # from the window, so the trailing high must not see it.
    repo = guarded_price_repo(
        bar(ticker=TICKER, date="2015-01-05", close=50),
        bar(ticker=TICKER, date="2015-01-10", close=80),
        bar(ticker=TICKER, date="2015-02-01", close=500),  # after as_of, higher
    )

    high = repo.get_trailing_high(TICKER, AsOfDate.parse("2015-01-15"), window_days=30)

    assert high == 80


def test_trailing_high_guard_catches_backend_that_leaks_a_future_bar() -> None:
    # A backend that gets its own date filtering wrong. The guard must raise
    # rather than silently compute a high from the leaked bar.
    leaking = LeakingPriceRepository(
        [
            bar(ticker=TICKER, date="2015-01-10", close=80),
            bar(ticker=TICKER, date="2015-02-01", close=500),  # after as_of
        ]
    )
    repo = wrap_price_repository(leaking)

    with pytest.raises(LookAheadError):
        repo.get_trailing_high(TICKER, AsOfDate.parse("2015-01-15"), window_days=30)


def test_trailing_high_returns_none_on_empty_window() -> None:
    repo = guarded_price_repo(bar(ticker=TICKER, date="2015-01-10", close=100))

    high = repo.get_trailing_high(TICKER, AsOfDate.parse("2014-01-01"), window_days=30)

    assert high is None
