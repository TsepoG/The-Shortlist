"""PHASE_0.md §6.6 — determinism.

The scoring engine (phase 4) depends on identical as-of inputs producing
identical output; that guarantee starts here. Delegates to
tests/contract/fact_repository_contract.py — see that module's docstring and
tests/unit/data/test_invariant.py for why.
"""

from tests.contract import fact_repository_contract as contract
from tests.fakes import guarded_fact_repo


def test_repeated_query_identical() -> None:
    contract.repeated_query_identical(guarded_fact_repo)


def test_tie_break_is_deterministic() -> None:
    contract.tie_break_is_deterministic(guarded_fact_repo)
