"""Coverage monitoring — PHASE_1.md §6, TESTING.md §1.5.

Tracks the fraction of the universe with a usable value, per concept, per
fiscal year (annual, `fp == "FY"`, facts only — coverage is a per-year
question, and the annual filing is what "does this company report this
concept" means in practice). Flags anything below 85%, and explicitly expects
thin pre-2011 coverage: XBRL was phased in from ~2009 for large accelerated
filers and ~2011 for smaller reporting companies (`TESTING.md` §1.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from shortlist.data.types import CanonicalConcept, Cik, Fact

COVERAGE_ALERT_THRESHOLD = Decimal("0.85")

# XBRL phase-in, stated directly on every report rather than left implicit —
# TESTING.md §1.5: "Explicitly document the XBRL phase-in."
XBRL_PHASE_IN_NOTE = (
    "XBRL was mandatory for large accelerated filers from ~2009 and for smaller "
    "reporting companies from ~2011. Coverage below the 85% threshold for years "
    "before 2011 is expected, not a defect; a backtest window starting before "
    "2011 must state this limitation (DESIGN.md §7a: tune on 2012-2019)."
)


@dataclass(frozen=True, slots=True)
class CoverageCell:
    """Coverage for one (concept, fiscal_year) cell."""

    concept: CanonicalConcept
    fiscal_year: int
    covered: int
    universe_size: int

    @property
    def fraction(self) -> Decimal:
        if self.universe_size == 0:
            return Decimal("0")
        return Decimal(self.covered) / Decimal(self.universe_size)

    @property
    def below_threshold(self) -> bool:
        return self.fraction < COVERAGE_ALERT_THRESHOLD


def compute_coverage(
    universe: Sequence[Cik],
    concepts: Sequence[CanonicalConcept],
    facts: Sequence[Fact],
) -> tuple[CoverageCell, ...]:
    """Coverage for every (concept, fiscal_year) combination that appears in
    `facts`, against the fixed `universe` size — not the number of companies
    that happen to have any facts at all, which would hide exactly the gap
    this job exists to catch.
    """
    universe_size = len(set(universe))
    concept_set = set(concepts)

    covering_ciks: dict[tuple[CanonicalConcept, int], set[Cik]] = {}
    years_seen: dict[CanonicalConcept, set[int]] = {}
    for fact in facts:
        if fact.fiscal_period != "FY" or fact.concept not in concept_set:
            continue
        covering_ciks.setdefault((fact.concept, fact.fiscal_year), set()).add(fact.cik)
        years_seen.setdefault(fact.concept, set()).add(fact.fiscal_year)

    cells: list[CoverageCell] = []
    for concept in concepts:
        for year in sorted(years_seen.get(concept, set())):
            covered = len(covering_ciks.get((concept, year), set()))
            cells.append(CoverageCell(concept, year, covered, universe_size))
    return tuple(cells)


def cells_below_threshold(cells: Sequence[CoverageCell]) -> tuple[CoverageCell, ...]:
    return tuple(c for c in cells if c.below_threshold)
