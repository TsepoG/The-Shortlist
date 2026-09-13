"""PHASE_1.md §7's required check: "Phase 0 guard tests pass unchanged against
the PostgreSQL implementation."

Every function here calls the identical, unmodified scenario from
`tests/contract/fact_repository_contract.py` that `tests/unit/data/` already
runs against the in-memory fake — see that module's docstring for why the
scenarios are defined once rather than duplicated. If a test here fails while
its unit counterpart passes, the defect is in `PostgresFactRepository`, not in
the invariant itself.
"""

from collections.abc import Callable

import pytest

from shortlist.data.repository import FactRepository
from tests.contract import fact_repository_contract as contract

pytestmark = pytest.mark.integration


def test_facts_filed_after_asof_are_excluded(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.facts_filed_after_asof_are_excluded(pg_fact_repo_factory)


def test_facts_filed_on_asof_are_included(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.facts_filed_on_asof_are_included(pg_fact_repo_factory)


def test_facts_filed_before_asof_are_included(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.facts_filed_before_asof_are_included(pg_fact_repo_factory)


def test_q4_2014_not_visible_before_filing(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.q4_2014_not_visible_before_filing(pg_fact_repo_factory)


def test_q4_2014_visible_after_filing(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.q4_2014_visible_after_filing(pg_fact_repo_factory)


def test_asof_before_restatement_returns_original(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.asof_before_restatement_returns_original(pg_fact_repo_factory)


def test_asof_after_restatement_returns_revision(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.asof_after_restatement_returns_revision(pg_fact_repo_factory)


def test_both_revisions_retrievable(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.both_revisions_retrievable(pg_fact_repo_factory)


def test_get_facts_for_universe_filters_by_asof(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.get_facts_for_universe_filters_by_asof(pg_fact_repo_factory)


def test_repeated_query_identical(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.repeated_query_identical(pg_fact_repo_factory)


def test_tie_break_is_deterministic(
    pg_fact_repo_factory: Callable[..., FactRepository],
) -> None:
    contract.tie_break_is_deterministic(pg_fact_repo_factory)
