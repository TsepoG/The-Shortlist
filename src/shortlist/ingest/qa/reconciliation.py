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

**A passing check is not always independent verification.** Since
`derive.py` gained `derive_total_liabilities`, a check can hold *by
construction* rather than by confirming two independently-tagged numbers
agree: `total_liabilities` is sometimes derived as
`LiabilitiesAndStockholdersEquity - stockholders_equity`, and confirmed
against every one of this phase's candidate periods,
`LiabilitiesAndStockholdersEquity == Assets` — so
`assets == liabilities + equity` is then arithmetically guaranteed to hold
(`Assets - equity + equity == Assets`), regardless of whether the filing's
numbers are actually sound. The same was already true, silently, for
`gross_profit`: a `GrossProfit` value derived as `revenue - cost_of_revenue`
makes `revenue - cost_of_revenue == gross_profit` trivially true.

`audit_identities` makes this visible instead of silently reporting a
tautological pass as if it were a real one: a bundle whose check holds but
includes a derived (`is_derived=True`) input is `CheckStatus.NOT_INDEPENDENT`,
distinct from `CheckStatus.VERIFIED` (holds, every input directly tagged from
the filing) and `CheckStatus.VIOLATION` (does not hold, within tolerance).
`reconcile()` is unchanged — it still returns only violations — implemented as
a filter over `audit_identities` so every existing caller keeps working.

Tolerance, per §6: relative 0.5% or absolute $1,000,000, whichever is larger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

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


class CheckStatus(StrEnum):
    """What an identity check's outcome for one period actually establishes."""

    VERIFIED = "verified"  # holds, and every input was directly tagged by the filer
    NOT_INDEPENDENT = "not_independent"  # holds, but an input is derived — tautological
    VIOLATION = "violation"  # does not hold, outside tolerance


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """One period's identity check result, whichever way it went.

    `derived_inputs` names which of the check's own concepts (in the same
    order the check declares them) were `is_derived=True` in the bundle used —
    empty for `VERIFIED` and always non-empty for `NOT_INDEPENDENT`; the field
    itself is what makes that distinction inspectable rather than implicit in
    the status alone.
    """

    cik: Cik
    period_end: date
    check: str
    status: CheckStatus
    expected: Decimal
    actual: Decimal
    difference: Decimal
    derived_inputs: tuple[CanonicalConcept, ...]


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


def _balance_sheet_identity(
    bundle: Mapping[CanonicalConcept, Fact],
) -> tuple[str, Decimal, Decimal]:
    """`assets == liabilities + equity`. Only ever called with a `bundle` the
    caller has already confirmed contains all of `_BALANCE_SHEET_CONCEPTS`.
    """
    assets = bundle[CanonicalConcept.TOTAL_ASSETS]
    liabilities = bundle[CanonicalConcept.TOTAL_LIABILITIES]
    equity = bundle[CanonicalConcept.STOCKHOLDERS_EQUITY]
    return (
        "assets_equal_liabilities_plus_equity",
        liabilities.value + equity.value,
        assets.value,
    )


def _gross_margin_identity(
    bundle: Mapping[CanonicalConcept, Fact],
) -> tuple[str, Decimal, Decimal]:
    """`revenue - cost_of_revenue == gross_profit`. Only ever called with a
    `bundle` the caller has already confirmed contains all of
    `_GROSS_MARGIN_CONCEPTS`.
    """
    revenue = bundle[CanonicalConcept.REVENUE]
    cost_of_revenue = bundle[CanonicalConcept.COST_OF_REVENUE]
    gross_profit = bundle[CanonicalConcept.GROSS_PROFIT]
    return (
        "revenue_minus_cost_of_revenue_equals_gross_profit",
        revenue.value - cost_of_revenue.value,
        gross_profit.value,
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


def audit_identities(facts: Sequence[Fact]) -> tuple[CheckOutcome, ...]:
    """Run every reconciliation check over `facts` and report **every**
    outcome — verified, not-independent, and violation alike — grouped by
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

    See the module docstring for why a passing check isn't always independent
    verification: `status` distinguishes `VERIFIED` from `NOT_INDEPENDENT`.
    """
    by_period: dict[tuple[Cik, date | None, date], list[Fact]] = {}
    for fact in facts:
        key = (fact.cik, fact.period_start, fact.period_end)
        by_period.setdefault(key, []).append(fact)

    outcomes: list[CheckOutcome] = []
    for (cik, _period_start, period_end), period_facts in by_period.items():
        for check, needed in (
            (_balance_sheet_identity, _BALANCE_SHEET_CONCEPTS),
            (_gross_margin_identity, _GROSS_MARGIN_CONCEPTS),
        ):
            bundle = _latest_self_consistent_bundle(period_facts, needed)
            if bundle is None:
                continue
            check_name, expected, actual = check(bundle)
            derived_inputs = tuple(c for c in needed if bundle[c].is_derived)
            if not _within_tolerance(expected, actual):
                status = CheckStatus.VIOLATION
            elif derived_inputs:
                status = CheckStatus.NOT_INDEPENDENT
            else:
                status = CheckStatus.VERIFIED
            outcomes.append(
                CheckOutcome(
                    cik=cik,
                    period_end=period_end,
                    check=check_name,
                    status=status,
                    expected=expected,
                    actual=actual,
                    difference=actual - expected,
                    derived_inputs=derived_inputs,
                )
            )

    return tuple(outcomes)


def reconcile(facts: Sequence[Fact]) -> tuple[ReconciliationViolation, ...]:
    """Every check that failed, within tolerance — a filter over
    `audit_identities` that discards `VERIFIED` and `NOT_INDEPENDENT`
    outcomes. Signature and behaviour are unchanged from before
    `audit_identities` existed, so every existing caller keeps working; a
    caller that also wants to distinguish a tautological pass from a real one
    should call `audit_identities` directly instead.
    """
    return tuple(
        ReconciliationViolation(
            cik=outcome.cik,
            period_end=outcome.period_end,
            check=outcome.check,
            expected=outcome.expected,
            actual=outcome.actual,
            difference=outcome.difference,
        )
        for outcome in audit_identities(facts)
        if outcome.status is CheckStatus.VIOLATION
    )
