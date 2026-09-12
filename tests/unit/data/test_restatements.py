"""PHASE_0.md §6.3 — restatements.

Delegates to tests/contract/fact_repository_contract.py — see that module's
docstring and tests/unit/data/test_invariant.py for why.
"""

from tests.contract import fact_repository_contract as contract
from tests.fakes import guarded_fact_repo


def test_asof_before_restatement_returns_original() -> None:
    contract.asof_before_restatement_returns_original(guarded_fact_repo)


def test_asof_after_restatement_returns_revision() -> None:
    contract.asof_after_restatement_returns_revision(guarded_fact_repo)


def test_both_revisions_retrievable() -> None:
    contract.both_revisions_retrievable(guarded_fact_repo)
