"""PHASE_0.md §6.5 — prices.

Delegates to `tests/contract/price_repository_contract.py` — see that
module's docstring and `tests/unit/data/test_invariant.py` for why shared
scenarios are defined once rather than duplicated per backend.

`test_trailing_high_guard_catches_backend_that_leaks_a_future_bar` stays here
rather than in the contract: it needs a deliberately broken backend
(`LeakingPriceRepository`) constructed directly, which has no meaningful
Postgres counterpart — the guard's job is to raise on a leak regardless of
which backend produced it, and that property is what this one test proves.
"""

import pytest

from shortlist.data.factory import wrap_price_repository
from shortlist.data.guard import LookAheadError
from shortlist.data.types import AsOfDate
from tests.contract import price_repository_contract as contract
from tests.fakes import guarded_price_repo
from tests.fakes.builders import bar
from tests.fakes.leaking_repository import LeakingPriceRepository

TICKER = "AAPL"


def test_price_bars_after_asof_excluded() -> None:
    contract.price_bars_after_asof_excluded(guarded_price_repo)


def test_trailing_high_window_ends_at_asof() -> None:
    contract.trailing_high_window_ends_at_asof(guarded_price_repo)


def test_trailing_high_returns_none_on_empty_window() -> None:
    contract.trailing_high_returns_none_on_empty_window(guarded_price_repo)


def test_trailing_high_uses_intraday_basis_not_closing_basis() -> None:
    # PHASE_2_NOTES.md §4's §0.2 decision, proved: max(adj_high), not
    # max(adj_close). Fails against the pre-phase-2 guard implementation.
    contract.trailing_high_uses_intraday_basis_not_closing_basis(guarded_price_repo)


def test_dip_ratio_is_invariant_across_a_subsequent_splits_rebasing() -> None:
    # PHASE_0_NOTES.md's "carried forward to phase 2" assumption, proved
    # rather than asserted by comment (PHASE_2.md §4.3).
    contract.dip_ratio_is_invariant_across_a_subsequent_splits_rebasing(guarded_price_repo)


def test_repeated_bars_query_identical() -> None:
    contract.repeated_bars_query_identical(guarded_price_repo)


def test_bars_are_sorted_by_ticker_then_date_and_returned_as_a_tuple() -> None:
    contract.bars_are_sorted_by_ticker_then_date_and_returned_as_a_tuple(guarded_price_repo)


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
