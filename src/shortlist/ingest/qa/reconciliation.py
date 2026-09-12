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

**Known limitation of the balance-sheet check**, found running the real phase 1
backfill: `assets == liabilities + equity` can show an expected, non-defect
"violation" for a company with material noncontrolling interest or
temporary/mezzanine equity — neither `minority_interest` nor `temporary_equity`
is among phase 1's 16 canonical concepts, and CLAUDE.md's "do not invent"
section rules out adding a concept mapping without being asked. Confirmed
against real filings: NVDA's balance sheet includes a
`TemporaryEquityValueExcludingAdditionalPaidInCapital` line that is neither
`total_liabilities` nor `stockholders_equity`, so this check reports a
violation there that is not a data defect. See `docs/phases/PHASE_1_NOTES.md`.

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

_BALANCE_SHEET_CONCEPTS = (
    CanonicalConcept.TOTAL_ASSETS,
    CanonicalConcept.TOTAL_LIABILITIES,
    CanonicalConcept.STOCKHOLDERS_EQUITY,
)
_GROSS_MARGIN_CONCEPTS = (
    CanonicalConcept.REVENUE,
    CanonicalConcept.COST_OF_REVENUE,
    CanonicalConcept.GROSS_PROFIT,
)


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


def _latest_self_consistent_bundle(
    facts: Sequence[Fact], needed: tuple[CanonicalConcept, ...]
) -> Mapping[CanonicalConcept, Fact] | None:
    """Among the accessions that report every concept in `needed` **together**
    for this period, return the bundle from the most-recently-filed one (ties
    broken by `accession_number` descending) — the same same-accession-only
    reasoning `derive.py` already uses for `gross_profit`, so a check never
    compares figures that no single filing ever asserted together.

    This matters because a company's later filing can re-report *one*
    concept for an older comparative period (e.g. a restated
    `stockholders_equity`) without re-reporting the others for that same
    period — collapsing each concept to its own independently-latest fact
    would then pair that restated figure against stale, unrelated
    assets/liabilities from an earlier filing. Confirmed against real data:
    this is exactly what happened for a Microsoft comparative period before
    this fix.
    """
    by_accession: dict[str, dict[CanonicalConcept, Fact]] = {}
    for f in facts:
        if f.concept in needed:
            by_accession.setdefault(f.accession_number, {})[f.concept] = f

    complete = {
        accession: bundle
        for accession, bundle in by_accession.items()
        if set(bundle) >= set(needed)
    }
    if not complete:
        return None
    _best_accession, best_bundle = max(
        complete.items(), key=lambda kv: (next(iter(kv[1].values())).filed_date, kv[0])
    )
    return best_bundle


def reconcile(facts: Sequence[Fact]) -> tuple[ReconciliationViolation, ...]:
    """Run every reconciliation check over `facts`, grouped by
    (cik, period_start, period_end); within each period, each check selects
    its own same-accession bundle via `_latest_self_consistent_bundle`.

    `period_start` is part of the grouping key, not just `period_end`: a
    single 10-K reports both the full-year and the Q4 figure for the same
    concept with an identical `period_end`, differing only in `period_start`
    (`None` for the FY duration or a balance-sheet instant, an earlier date
    for the Q4 duration). Grouping on `period_end` alone would mix FY revenue
    with Q4 cost-of-revenue — the same collision `PHASE_1_NOTES.md` §0.1
    already fixed in the unique constraint. Balance-sheet instants all carry
    `period_start IS NULL`, so they still group together correctly.

    `facts` should be every as-of-a-date visible fact for the universe —
    restatements and superseded values included, **not** pre-collapsed to one
    fact per concept. This function does no as-of filtering itself; that is
    the caller's job via the guarded `FactRepository`.
    """
    by_period: dict[tuple[Cik, date | None, date], list[Fact]] = {}
    for fact in facts:
        key = (fact.cik, fact.period_start, fact.period_end)
        by_period.setdefault(key, []).append(fact)

    violations: list[ReconciliationViolation] = []
    for (cik, _period_start, period_end), period_facts in by_period.items():
        for check, needed in (
            (_check_balance_sheet_identity, _BALANCE_SHEET_CONCEPTS),
            (_check_gross_margin_identity, _GROSS_MARGIN_CONCEPTS),
        ):
            bundle = _latest_self_consistent_bundle(period_facts, needed)
            if bundle is None:
                continue
            violation = check(cik, period_end, bundle)
            if violation is not None:
                violations.append(violation)

    return tuple(violations)
