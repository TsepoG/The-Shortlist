"""Reconciliation checks — PHASE_1.md §6, TESTING.md §1.4.

Flags violations; never auto-corrects (§6: "flag violations, do not
auto-correct"). Pure functions over already-fetched facts, so the logic is
unit-testable without a database; a thin CLI job (not built in this phase's
required scope) would fetch a universe's latest facts as of a date and call
`reconcile`.

Checks implemented, exactly the three PHASE_1.md §6 authorizes for phase 1:

- `assets == liabilities + equity`
- `revenue - cost_of_revenue == gross_profit` where all three present
- (four-quarters-sum-to-annual and the cash-flow tie are **not** implemented
  here — see `docs/phases/PHASE_1_NOTES.md` assumptions 6 and 7 for why: EDGAR
  frequently omits an explicit Q4 tag, and the cash-flow tie needs
  investing/financing concepts that are not among phase 1's 16 canonical ones.
  Adding either without the missing pieces would mean inventing a check the
  spec didn't ask for, which CLAUDE.md's "do not invent" section rules out.)

Tolerance, per §6: relative 0.5% or absolute $1,000,000, whichever is larger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik, Fact

RELATIVE_TOLERANCE = Decimal("0.005")
ABSOLUTE_TOLERANCE = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class ReconciliationViolation:
    """One period's identity check that didn't hold, within tolerance."""

    cik: Cik
    period_end: date
    check: str
    expected: Decimal
    actual: Decimal
    difference: Decimal


def _tolerance(reference: Decimal) -> Decimal:
    return max(abs(reference) * RELATIVE_TOLERANCE, ABSOLUTE_TOLERANCE)


def _within_tolerance(expected: Decimal, actual: Decimal) -> bool:
    return abs(actual - expected) <= _tolerance(expected)


def _check_balance_sheet_identity(
    cik: Cik, period_end: date, by_concept: Mapping[CanonicalConcept, Fact]
) -> ReconciliationViolation | None:
    assets = by_concept.get(CanonicalConcept.TOTAL_ASSETS)
    liabilities = by_concept.get(CanonicalConcept.TOTAL_LIABILITIES)
    equity = by_concept.get(CanonicalConcept.STOCKHOLDERS_EQUITY)
    if assets is None or liabilities is None or equity is None:
        return None  # not all three present for this period — not checkable

    expected = liabilities.value + equity.value
    if _within_tolerance(expected, assets.value):
        return None
    return ReconciliationViolation(
        cik=cik,
        period_end=period_end,
        check="assets_equal_liabilities_plus_equity",
        expected=expected,
        actual=assets.value,
        difference=assets.value - expected,
    )


def _check_gross_margin_identity(
    cik: Cik, period_end: date, by_concept: Mapping[CanonicalConcept, Fact]
) -> ReconciliationViolation | None:
    revenue = by_concept.get(CanonicalConcept.REVENUE)
    cost_of_revenue = by_concept.get(CanonicalConcept.COST_OF_REVENUE)
    gross_profit = by_concept.get(CanonicalConcept.GROSS_PROFIT)
    if revenue is None or cost_of_revenue is None or gross_profit is None:
        return None

    expected = revenue.value - cost_of_revenue.value
    if _within_tolerance(expected, gross_profit.value):
        return None
    return ReconciliationViolation(
        cik=cik,
        period_end=period_end,
        check="revenue_minus_cost_of_revenue_equals_gross_profit",
        expected=expected,
        actual=gross_profit.value,
        difference=gross_profit.value - expected,
    )


def reconcile(facts: Sequence[Fact]) -> tuple[ReconciliationViolation, ...]:
    """Run every reconciliation check over `facts`, grouped by (cik, period_end).

    `facts` should be the already-resolved, as-of-a-date latest fact per
    (cik, concept, period_end) — this function does no as-of filtering itself;
    that is the caller's job via the guarded `FactRepository`.
    """
    by_period: dict[tuple[Cik, date], dict[CanonicalConcept, Fact]] = {}
    for fact in facts:
        by_period.setdefault((fact.cik, fact.period_end), {})[fact.concept] = fact

    violations: list[ReconciliationViolation] = []
    for (cik, period_end), by_concept in by_period.items():
        for check in (_check_balance_sheet_identity, _check_gross_margin_identity):
            violation = check(cik, period_end, by_concept)
            if violation is not None:
                violations.append(violation)

    return tuple(violations)
