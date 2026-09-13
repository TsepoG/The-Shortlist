"""PHASE_0.md §6.2 — the exact adversarial scenario from TESTING.md §1.2.

Delegates to tests/contract/fact_repository_contract.py — see that module's
docstring and tests/unit/data/test_invariant.py for why.
"""

from tests.contract import fact_repository_contract as contract
from tests.fakes import guarded_fact_repo


def test_q4_2014_not_visible_before_filing() -> None:
    contract.q4_2014_not_visible_before_filing(guarded_fact_repo)


def test_q4_2014_visible_after_filing() -> None:
    contract.q4_2014_visible_after_filing(guarded_fact_repo)
