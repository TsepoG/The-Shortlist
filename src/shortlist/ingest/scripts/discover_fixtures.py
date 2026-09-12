"""Fixture discovery for PHASE_1.md §4's four "identify empirically" cases:
custom extension tags, a restatement, a fiscal year change, and a smaller
reporting company.

**This script surfaces ranked candidates; it does not choose, and it hardcodes
no company.** PHASE_1.md §4 is explicit: "inventing a plausible-sounding
example here would be worse than finding a real one." Each candidate carries
enough (CIK, accession numbers, differing values, a filing URL) to be
hand-verified with one click — the actual choice, and the golden-value
hardcoding that follows it, is a human step this script does not take.

Every function here is pure over already-fetched facts/unmapped-tags — no I/O,
no network — so the ranking logic is unit-testable on synthetic data. The CLI
entry point (`main`) is the thin, untested wrapper that fetches from Postgres
and calls `qa.report.write_report`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from shortlist.data.types import CanonicalConcept, Cik, Fact
from shortlist.ingest.companyfacts import UnmappedTag
from shortlist.ingest.qa.reconciliation import ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE


def filing_index_url(cik: Cik, accession_number: str) -> str:
    """A direct link to a filing's index page on EDGAR, for one-click review."""
    cik_no_leading_zeros = str(int(cik.value))
    accession_no_dashes = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/{accession_no_dashes}/"


# --- Restatement candidates ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestatementCandidate:
    cik: Cik
    concept: CanonicalConcept
    period_end: date
    earlier_accession: str
    earlier_value: Decimal
    later_accession: str
    later_value: Decimal
    difference: Decimal
    filing_url: str


def find_restatement_candidates(facts: Sequence[Fact]) -> tuple[RestatementCandidate, ...]:
    """Companies with two filings covering the same period, materially
    different values — PHASE_1.md §4's restatement case. Ranked by the size of
    the difference, largest first.
    """
    by_period: dict[tuple[Cik, CanonicalConcept, date | None, date], dict[str, Fact]] = {}
    for f in facts:
        key = (f.cik, f.concept, f.period_start, f.period_end)
        by_period.setdefault(key, {})[f.accession_number] = f

    candidates: list[RestatementCandidate] = []
    for (cik, concept, _period_start, period_end), by_accession in by_period.items():
        if len(by_accession) < 2:
            continue
        ordered = sorted(by_accession.values(), key=lambda f: f.filed_date)
        earliest, latest = ordered[0], ordered[-1]
        tolerance = max(abs(earliest.value) * RELATIVE_TOLERANCE, ABSOLUTE_TOLERANCE)
        difference = latest.value - earliest.value
        if abs(difference) <= tolerance:
            continue
        candidates.append(
            RestatementCandidate(
                cik=cik,
                concept=concept,
                period_end=period_end,
                earlier_accession=earliest.accession_number,
                earlier_value=earliest.value,
                later_accession=latest.accession_number,
                later_value=latest.value,
                difference=difference,
                filing_url=filing_index_url(cik, latest.accession_number),
            )
        )

    return tuple(sorted(candidates, key=lambda c: -abs(c.difference)))


# --- Fiscal year change candidates ---------------------------------------------


@dataclass(frozen=True, slots=True)
class FiscalYearChangeCandidate:
    cik: Cik
    earlier_fiscal_year: int
    earlier_period_end: date
    later_fiscal_year: int
    later_period_end: date
    filing_url: str


def find_fiscal_year_change_candidates(
    facts: Sequence[Fact],
) -> tuple[FiscalYearChangeCandidate, ...]:
    """Companies whose annual (`fp == "FY"`) `period_end` month shifts between
    consecutive fiscal years — PHASE_1.md §4: "identify empirically by scanning
    for a company whose period_end month shifts between annual filings."
    """
    annual_by_cik: dict[Cik, dict[int, Fact]] = {}
    for f in facts:
        if f.fiscal_period != "FY":
            continue
        annual_by_cik.setdefault(f.cik, {})[f.fiscal_year] = f

    candidates: list[FiscalYearChangeCandidate] = []
    for cik, by_year in annual_by_cik.items():
        years = sorted(by_year)
        for earlier_year, later_year in pairwise(years):
            earlier = by_year[earlier_year]
            later = by_year[later_year]
            if earlier.period_end.month != later.period_end.month:
                candidates.append(
                    FiscalYearChangeCandidate(
                        cik=cik,
                        earlier_fiscal_year=earlier_year,
                        earlier_period_end=earlier.period_end,
                        later_fiscal_year=later_year,
                        later_period_end=later.period_end,
                        filing_url=filing_index_url(cik, later.accession_number),
                    )
                )

    return tuple(candidates)


# --- Smaller reporting company candidates --------------------------------------


@dataclass(frozen=True, slots=True)
class SmallerReportingCompanyCandidate:
    cik: Cik
    first_filed_date: date
    concept_coverage_fraction: Decimal


def find_smaller_reporting_company_candidates(
    facts: Sequence[Fact],
    concepts: Sequence[CanonicalConcept],
) -> tuple[SmallerReportingCompanyCandidate, ...]:
    """The thin-XBRL signature PHASE_1.md §4 describes: a late first `filed_date`
    combined with below-median concept coverage. Ranked by latest first-filed
    date, then lowest coverage — the companies most likely to show the pattern
    first.
    """
    first_filed: dict[Cik, date] = {}
    concepts_seen: dict[Cik, set[CanonicalConcept]] = {}
    concept_set = set(concepts)
    for f in facts:
        if f.concept not in concept_set:
            continue
        first_filed[f.cik] = min(first_filed.get(f.cik, f.filed_date), f.filed_date)
        concepts_seen.setdefault(f.cik, set()).add(f.concept)

    if not concepts_seen:
        return ()

    coverage_by_cik = {
        cik: Decimal(len(seen)) / Decimal(len(concept_set)) for cik, seen in concepts_seen.items()
    }
    sorted_coverages = sorted(coverage_by_cik.values())
    median_coverage = sorted_coverages[len(sorted_coverages) // 2]

    candidates = [
        SmallerReportingCompanyCandidate(
            cik=cik,
            first_filed_date=first_filed[cik],
            concept_coverage_fraction=coverage_by_cik[cik],
        )
        for cik in coverage_by_cik
        if coverage_by_cik[cik] < median_coverage
    ]

    def _sort_key(c: SmallerReportingCompanyCandidate) -> tuple[int, Decimal]:
        return (-c.first_filed_date.toordinal(), c.concept_coverage_fraction)

    return tuple(sorted(candidates, key=_sort_key))


# --- Custom extension tag candidates -------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomTagCandidate:
    namespace: str
    tag: str
    count: int
    plausible_concept_hint: str


# Substrings that suggest a custom tag might be standing in for a core concept
# a company chose not to tag with the standard us-gaap alias — a hint for a
# human reviewer, never an automatic mapping. PHASE_1.md §3: custom tags are
# "not silently mapped."
_CONCEPT_HINT_SUBSTRINGS: dict[str, str] = {
    "revenue": "revenue",
    "sales": "revenue",
    "netincome": "net_income",
    "earnings": "net_income",
    "assets": "total_assets",
    "liabilities": "total_liabilities",
}


def find_custom_tag_candidates(
    unmapped_tags: Sequence[UnmappedTag],
) -> tuple[CustomTagCandidate, ...]:
    """Unmapped tags whose name plausibly stands in for a core concept —
    PHASE_1.md §4: "run the alias chain across the universe and pick a company
    where revenue resolves only via a non-standard tag." Ranked by frequency.
    """
    candidates: list[CustomTagCandidate] = []
    for t in unmapped_tags:
        lowered = t.tag.lower()
        for substring, hint in _CONCEPT_HINT_SUBSTRINGS.items():
            if substring in lowered:
                candidates.append(CustomTagCandidate(t.namespace, t.tag, t.count, hint))
                break
    return tuple(sorted(candidates, key=lambda c: -c.count))
