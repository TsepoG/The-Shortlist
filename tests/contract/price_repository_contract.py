"""PHASE_0.md §6.5/§6.6 scenarios for `PriceReader`, backend-independent —
mirrors `fact_repository_contract.py`'s structure exactly.

Each function takes a `make_repo` factory shaped like
`tests.fakes.guarded_price_repo`: `(*bars: PriceBar) -> PriceReader`, already
wrapped by the real guard. How those bars got there (an in-memory list, or a
real INSERT into Postgres) is the only thing that differs between callers.

**Not included here**: `test_trailing_high_guard_catches_backend_that_leaks_a_future_bar`
(PHASE_0.md §6.5) stays unit-only in `tests/unit/data/test_prices.py` — it
needs a deliberately broken backend (`LeakingPriceRepository`) constructed
directly, not a correctly-behaving `make_repo` factory, so it has no
meaningful Postgres counterpart (Postgres can't be told to "leak" a bar
outside its own WHERE clause the way an in-memory fake can).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from shortlist.data.repository import PriceReader
from shortlist.data.types import AsOfDate
from tests.fakes.builders import bar

PriceRepoFactory = Callable[..., PriceReader]

# Not a real company's ticker, deliberately: this contract runs against real
# Postgres too, and a real symbol would risk colliding with genuine backfilled
# data sitting in the same dev database outside this test's transaction — the
# exact collision found and fixed in docs/phases/PHASE_2_NOTES.md's "found and
# fixed while running the real backfill" note.
TICKER = "ZZZCONTRACT"


# --- §6.5 Prices ---------------------------------------------------------------


def price_bars_after_asof_excluded(make_repo: PriceRepoFactory) -> None:
    repo = make_repo(
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


def trailing_high_window_ends_at_asof(make_repo: PriceRepoFactory) -> None:
    # A higher price exists after as_of; a correct backend already excludes it
    # from the window, so the trailing high must not see it.
    repo = make_repo(
        bar(ticker=TICKER, date="2015-01-05", close=50),
        bar(ticker=TICKER, date="2015-01-10", close=80),
        bar(ticker=TICKER, date="2015-02-01", close=500),  # after as_of, higher
    )

    high = repo.get_trailing_high(TICKER, AsOfDate.parse("2015-01-15"), window_days=30)

    assert high == 80


def trailing_high_returns_none_on_empty_window(make_repo: PriceRepoFactory) -> None:
    repo = make_repo(bar(ticker=TICKER, date="2015-01-10", close=100))

    high = repo.get_trailing_high(TICKER, AsOfDate.parse("2014-01-01"), window_days=30)

    assert high is None


def trailing_high_uses_intraday_basis_not_closing_basis(make_repo: PriceRepoFactory) -> None:
    """The §0.2 decision, proved against behaviour, not just against the
    fixture default. A bar whose `adj_high` exceeds every `adj_close` in the
    window must make `get_trailing_high` return that higher `adj_high` — this
    fails against the pre-phase-2 `max(adj_close)` implementation, unlike
    every other scenario here, which passes either way because `bar()`
    defaults `adj_high == adj_close` when unset.
    """
    repo = make_repo(
        bar(ticker=TICKER, date="2015-01-05", close=100, adj_close=100, adj_high=100),
        # A wick: intraday high well above the close, on the adjusted basis.
        bar(ticker=TICKER, date="2015-01-10", close=105, adj_close=105, adj_high=140),
    )

    high = repo.get_trailing_high(TICKER, AsOfDate.parse("2015-01-15"), window_days=30)

    assert high == 140  # not 105 — the closing-basis answer


def dip_ratio_is_invariant_across_a_subsequent_splits_rebasing(make_repo: PriceRepoFactory) -> None:
    """PHASE_0_NOTES.md's carried-forward-to-phase-2 assumption, proved rather
    than asserted by comment: the dip ratio `(trailing_high - current) /
    trailing_high` must be identical whether computed on a pre-split view of a
    window or on the SAME window after a subsequent split has uniformly
    re-based every value in it (exactly what the phase 2 upsert does — see
    `docs/phases/PHASE_2_NOTES.md` §0.1). If post-as-of adjustments didn't
    cancel in the ratio, a stock's measured "dip %" would silently change
    every time it split, with no change in the underlying facts.
    """
    pre_split = make_repo(
        bar(ticker=TICKER, date="2015-01-05", close=100, adj_close=100, adj_high=100),
        bar(ticker=TICKER, date="2015-01-10", close=80, adj_close=80, adj_high=85),
    )
    as_of = AsOfDate.parse("2015-01-15")
    pre_high = pre_split.get_trailing_high(TICKER, as_of, window_days=30)
    pre_current = pre_split.get_bars(TICKER, dt.date(2015, 1, 10), dt.date(2015, 1, 10), as_of)[
        0
    ].adj_close
    assert pre_high is not None
    pre_ratio = (pre_high - pre_current) / pre_high

    # A 10:1 split occurring after as_of re-bases every value in the SAME
    # window by the same factor — simulating what a real re-ingestion does.
    factor = Decimal(10)
    post_split = make_repo(
        bar(
            ticker=TICKER,
            date="2015-01-05",
            close=Decimal(100) / factor,
            adj_close=Decimal(100) / factor,
            adj_high=Decimal(100) / factor,
        ),
        bar(
            ticker=TICKER,
            date="2015-01-10",
            close=Decimal(80) / factor,
            adj_close=Decimal(80) / factor,
            adj_high=Decimal(85) / factor,
        ),
    )
    post_high = post_split.get_trailing_high(TICKER, as_of, window_days=30)
    post_current = post_split.get_bars(TICKER, dt.date(2015, 1, 10), dt.date(2015, 1, 10), as_of)[
        0
    ].adj_close
    assert post_high is not None
    post_ratio = (post_high - post_current) / post_high

    assert pre_ratio == post_ratio


# --- §6.6 Determinism -----------------------------------------------------------


def repeated_bars_query_identical(make_repo: PriceRepoFactory) -> None:
    repo = make_repo(
        bar(ticker=TICKER, date="2015-01-05", close=100),
        bar(ticker=TICKER, date="2015-01-10", close=110),
    )
    as_of = AsOfDate.parse("2015-01-15")

    first = repo.get_bars(TICKER, dt.date(2015, 1, 1), dt.date(2015, 1, 15), as_of)
    second = repo.get_bars(TICKER, dt.date(2015, 1, 1), dt.date(2015, 1, 15), as_of)

    assert first == second


def bars_are_sorted_by_ticker_then_date_and_returned_as_a_tuple(
    make_repo: PriceRepoFactory,
) -> None:
    repo = make_repo(
        bar(ticker=TICKER, date="2015-01-10", close=110),
        bar(ticker=TICKER, date="2015-01-05", close=100),  # inserted out of order
    )

    result = repo.get_bars(
        TICKER, dt.date(2015, 1, 1), dt.date(2015, 1, 15), AsOfDate.parse("2015-01-15")
    )

    assert isinstance(result, tuple)
    assert [b.date.isoformat() for b in result] == ["2015-01-05", "2015-01-10"]
