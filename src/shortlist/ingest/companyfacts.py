"""Parses one company's `companyfacts` JSON into `Fact` rows.

Pure function, no I/O — PHASE_1.md §7: "Unit (no network — use recorded fixture
JSON)". Given the JSON structure `data.sec.gov/api/xbrl/companyfacts/CIK##.json`
returns, walks every observation under every namespace/tag/unit this module's
alias chains (`concepts.py`) claim, and produces:

- `facts`: successfully parsed and normalized `Fact` rows (including derived
  `gross_profit` rows, added by the caller — see `derive.py`)
- `unmapped_tags`: every (namespace, tag) this parser saw but no alias chain
  claimed, with a frequency count — the raw material for PHASE_1.md §6's
  unmapped-tag report
- `rejections`: observations dropped, each with a reason — missing `filed`,
  a unit mismatch, or a non-USD unit — so PHASE_1.md §7's "a record missing
  filed is dropped, not defaulted" is something a test can assert on directly,
  not something buried in a log line.

PHASE_1.md §2: `filed` -> `Fact.filed_date` and "must never be inferred,
defaulted, or reconstructed from the period. If a record lacks filed, drop it
and log — do not guess." That rule is load-bearing for every phase 0 invariant
downstream, so it is enforced here, at the one place a `filed_date` is created
from raw input.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from shortlist.data.types import CanonicalConcept, Cik, Fact, Unit
from shortlist.ingest.concepts import concept_for_tag, expected_unit, resolve_first_available
from shortlist.ingest.derive import derive_gross_profit

# XBRL unit keys as they appear in companyfacts JSON, mapped to our Unit enum.
# companyfacts uses these unit strings verbatim; TESTING.md §1.3: "USD vs
# USD/shares vs pure are never mixed."
_UNIT_BY_XBRL_KEY: dict[str, Unit] = {
    "USD": Unit.USD,
    "shares": Unit.SHARES,
    "USD/shares": Unit.USD_PER_SHARE,
    "pure": Unit.PURE,
}


def _looks_like_currency_code(xbrl_unit: str) -> bool:
    """True for a plausible ISO 4217 code (EUR, JPY, GBP, ...) — three uppercase
    letters, and not one of the non-currency unit strings XBRL also uses.
    """
    return len(xbrl_unit) == 3 and xbrl_unit.isalpha() and xbrl_unit.isupper()


@dataclass(frozen=True, slots=True)
class Rejection:
    """One dropped observation, with why. Never silently discarded."""

    namespace: str
    tag: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class UnmappedTag:
    """A (namespace, tag) no alias chain claims, and how often it appeared."""

    namespace: str
    tag: str
    count: int


@dataclass(frozen=True, slots=True)
class ParseResult:
    facts: tuple[Fact, ...]
    unmapped_tags: tuple[UnmappedTag, ...]
    rejections: tuple[Rejection, ...]


@dataclass
class _Accumulator:
    facts: list[Fact] = field(default_factory=list)
    unmapped_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    rejections: list[Rejection] = field(default_factory=list)

    def reject(self, namespace: str, tag: str, reason: str, detail: str) -> None:
        self.rejections.append(Rejection(namespace, tag, reason, detail))

    def result(self) -> ParseResult:
        unmapped = tuple(
            UnmappedTag(namespace, tag, count)
            for (namespace, tag), count in sorted(
                self.unmapped_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        return ParseResult(tuple(self.facts), unmapped, tuple(self.rejections))


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_companyfacts(cik: Cik, payload: dict[str, Any]) -> ParseResult:
    """Parse one company's full `companyfacts` document.

    `payload` is the JSON object as SEC EDGAR returns it: a top-level `facts`
    key holding `{namespace: {tag: {"units": {unit: [observation, ...]}}}}`.
    Namespaces other than `us-gaap` and `dei` (custom company extensions) are
    walked the same way — if their tag happens to match a canonical alias
    verbatim it will not, since alias namespaces are `us-gaap`/`dei` only, so
    every custom-namespace observation always lands in `unmapped_tags`, per
    PHASE_1.md §3: "Custom extension tags ... are not silently mapped."

    Two passes: first group every observation that resolves to a concept by
    `(concept, period_start, period_end, accession_number)`, since PHASE_1.md
    §3's "try each in order, take the first that yields a value for the
    period" needs to see every competing alias for one exact period+accession
    before deciding — deciding tag-by-tag as encountered would let whichever
    alias happens to load first from the JSON win, not the highest-priority
    one. Second pass resolves each group via `resolve_first_available` and
    validates only the winner; every alias that loses is recorded as a
    rejection, never silently discarded and never written as a second,
    conflicting `Fact` left for the database to arbitrate.
    """
    acc = _Accumulator()
    facts_by_namespace = payload.get("facts", {})

    _CandidateKey = tuple[CanonicalConcept, object, object, object]
    _Candidate = tuple[str, dict[str, Any]]  # (xbrl_unit, obs)
    candidates: dict[_CandidateKey, dict[tuple[str, str], _Candidate]] = {}

    for namespace, tags in facts_by_namespace.items():
        for tag, tag_body in tags.items():
            concept = concept_for_tag(namespace, tag)
            units = tag_body.get("units", {})
            if concept is None:
                for observations in units.values():
                    acc.unmapped_counts[(namespace, tag)] += len(observations)
                continue
            for xbrl_unit, observations in units.items():
                for obs in observations:
                    key: _CandidateKey = (
                        concept,
                        obs.get("start"),
                        obs.get("end"),
                        obs.get("accn"),
                    )
                    candidates.setdefault(key, {})[(namespace, tag)] = (xbrl_unit, obs)

    for (concept, _start, _end, _accn), by_alias in candidates.items():
        # dict's invariance means `by_alias` (value type `_Candidate`) isn't
        # directly assignable to resolve_first_available's `dict[..., object]`
        # parameter, and its return value is typed as `object` for the same
        # reason — rebuild as a plain object-valued dict and cast back.
        available: dict[tuple[str, str], object] = dict(by_alias)
        chosen = resolve_first_available(concept, available)
        assert chosen is not None  # every group has at least one candidate by construction
        chosen_alias, chosen_value = chosen
        chosen_unit, chosen_obs = cast(_Candidate, chosen_value)
        for (namespace, tag), _candidate in by_alias.items():
            if (namespace, tag) == (chosen_alias.namespace, chosen_alias.tag):
                continue
            acc.reject(
                namespace,
                tag,
                "shadowed_by_higher_priority_alias",
                f"concept={concept.value!r} superseded_by={chosen_alias.tag!r}",
            )
        _parse_observation(
            acc, cik, chosen_alias.namespace, chosen_alias.tag, concept, chosen_unit, chosen_obs
        )

    _derive_missing_gross_profit(acc)

    return acc.result()


def _parse_observation(
    acc: _Accumulator,
    cik: Cik,
    namespace: str,
    tag: str,
    concept: CanonicalConcept | None,
    xbrl_unit: str,
    obs: dict[str, Any],
) -> None:
    if concept is None:
        acc.unmapped_counts[(namespace, tag)] += 1
        return

    filed_date = _parse_date(obs.get("filed"))
    if filed_date is None:
        # PHASE_1.md §2: never inferred, defaulted, or reconstructed. Dropped,
        # not guessed — a record without `filed` cannot be point-in-time-safe.
        acc.reject(namespace, tag, "missing_filed", f"accn={obs.get('accn')!r}")
        return

    period_end = _parse_date(obs.get("end"))
    if period_end is None:
        acc.reject(namespace, tag, "missing_period_end", f"accn={obs.get('accn')!r}")
        return

    period_start = _parse_date(obs.get("start"))  # None is valid: balance-sheet instant

    value = _parse_decimal(obs.get("val"))
    if value is None:
        acc.reject(namespace, tag, "unparseable_value", f"val={obs.get('val')!r}")
        return

    accession_number = obs.get("accn")
    if not accession_number:
        acc.reject(namespace, tag, "missing_accession", f"filed={filed_date.isoformat()}")
        return

    want_unit = expected_unit(concept)

    if want_unit is Unit.USD and xbrl_unit != "USD":
        if _looks_like_currency_code(xbrl_unit):
            # PHASE_1.md §3: "Non-USD units: log and skip for now." — a real
            # foreign-currency amount (EUR, JPY, ...), not a type mismatch.
            acc.reject(namespace, tag, "non_usd_unit", f"unit={xbrl_unit!r}")
        else:
            # Not USD and not a plausible currency either — a monetary concept
            # tagged in shares, "pure", or something unrecognized entirely.
            # PHASE_1.md §3: "reject rows whose unit does not match the
            # concept's expected unit."
            acc.reject(namespace, tag, "unit_mismatch", f"expected='USD' got={xbrl_unit!r}")
        return

    unit = _UNIT_BY_XBRL_KEY.get(xbrl_unit)
    if unit is None or unit is not want_unit:
        acc.reject(
            namespace,
            tag,
            "unit_mismatch",
            f"expected={want_unit.value!r} got={xbrl_unit!r}",
        )
        return

    form = obs.get("form")
    fiscal_year = obs.get("fy")
    fiscal_period = obs.get("fp")
    if not isinstance(form, str) or not isinstance(fiscal_year, int) or not fiscal_period:
        acc.reject(namespace, tag, "missing_report_metadata", f"accn={accession_number!r}")
        return

    acc.facts.append(
        Fact(
            cik=cik,
            concept=concept,
            raw_tag=tag,
            unit=unit,
            value=value,
            period_start=period_start,
            period_end=period_end,
            fiscal_year=fiscal_year,
            fiscal_period=str(fiscal_period),
            form=form,
            filed_date=filed_date,
            accession_number=str(accession_number),
        )
    )


def _derive_missing_gross_profit(acc: _Accumulator) -> None:
    """Derive gross_profit for every (accession, period) that has revenue and
    cost_of_revenue but no explicitly tagged gross_profit — as-filed always
    wins, so a period that already resolved a tagged GrossProfit is skipped.
    """
    by_key: dict[tuple[str, date | None, date], dict[CanonicalConcept, Fact]] = {}
    for f in acc.facts:
        if f.concept not in (
            CanonicalConcept.REVENUE,
            CanonicalConcept.COST_OF_REVENUE,
            CanonicalConcept.GROSS_PROFIT,
        ):
            continue
        key = (f.accession_number, f.period_start, f.period_end)
        by_key.setdefault(key, {})[f.concept] = f

    derived: list[Fact] = []
    for concepts_present in by_key.values():
        if CanonicalConcept.GROSS_PROFIT in concepts_present:
            continue  # as-filed already present; never overridden by a derivation
        revenue = concepts_present.get(CanonicalConcept.REVENUE)
        cost_of_revenue = concepts_present.get(CanonicalConcept.COST_OF_REVENUE)
        if revenue is None or cost_of_revenue is None:
            continue
        derived_fact = derive_gross_profit(revenue, cost_of_revenue)
        if derived_fact is not None:
            derived.append(derived_fact)

    acc.facts.extend(derived)
