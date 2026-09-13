"""PHASE_2.md §5's required check: "Phase 0 `PriceReader` guard tests pass
unchanged against the new PostgreSQL-backed implementation."

Every function here calls the identical, unmodified scenario from
`tests/contract/price_repository_contract.py` that `tests/unit/data/` already
runs against the in-memory fake — see that module's docstring for why the
scenarios are defined once rather than duplicated. If a test here fails while
its unit counterpart passes, the defect is in `PostgresPriceRepository`, not
in the invariant itself.
"""

from collections.abc import Callable

import pytest

from shortlist.data.repository import PriceReader
from tests.contract import price_repository_contract as contract

pytestmark = pytest.mark.integration


def test_price_bars_after_asof_excluded(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.price_bars_after_asof_excluded(pg_price_repo_factory)


def test_trailing_high_window_ends_at_asof(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.trailing_high_window_ends_at_asof(pg_price_repo_factory)


def test_trailing_high_returns_none_on_empty_window(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.trailing_high_returns_none_on_empty_window(pg_price_repo_factory)


def test_trailing_high_uses_intraday_basis_not_closing_basis(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.trailing_high_uses_intraday_basis_not_closing_basis(pg_price_repo_factory)


def test_dip_ratio_is_invariant_across_a_subsequent_splits_rebasing(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.dip_ratio_is_invariant_across_a_subsequent_splits_rebasing(pg_price_repo_factory)


def test_repeated_bars_query_identical(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.repeated_bars_query_identical(pg_price_repo_factory)


def test_bars_are_sorted_by_ticker_then_date_and_returned_as_a_tuple(
    pg_price_repo_factory: Callable[..., PriceReader],
) -> None:
    contract.bars_are_sorted_by_ticker_then_date_and_returned_as_a_tuple(pg_price_repo_factory)
