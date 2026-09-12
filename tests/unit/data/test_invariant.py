"""PHASE_0.md §6.1 — the core invariant, in its simplest form.

Delegates to tests/contract/fact_repository_contract.py, run here against the
in-memory fake (no DB, no network) and again in tests/integration/ against
PostgresFactRepository — see PHASE_1.md §7 and tests/contract/__init__.py.
"""

from tests.contract import fact_repository_contract as contract
from tests.fakes import guarded_fact_repo


def test_facts_filed_after_asof_are_excluded() -> None:
    contract.facts_filed_after_asof_are_excluded(guarded_fact_repo)


def test_facts_filed_on_asof_are_included() -> None:
    contract.facts_filed_on_asof_are_included(guarded_fact_repo)


def test_facts_filed_before_asof_are_included() -> None:
    contract.facts_filed_before_asof_are_included(guarded_fact_repo)
