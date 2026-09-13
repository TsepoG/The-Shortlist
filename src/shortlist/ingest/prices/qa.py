"""Price quality jobs — PHASE_2.md §4.

Two pure, unit-testable checks over already-fetched bars, mirroring phase 1's
`qa/` pattern: no I/O here, a thin caller (`run_phase2.py`, not built as part
of this required scope beyond what's needed to produce report artifacts)
fetches a universe's bars and calls these, then archives the result via
`shortlist.ingest.qa.report.write_report` — the same job phase 1 already
built and this module reuses rather than duplicating.

**§4.1 known corporate action tests.** `KNOWN_EVENTS` are the four PHASE_2.md
§4.1 requires, each verified against the provider's own events feed before
being hardcoded here (`docs/phases/PHASE_2_NOTES.md` §1) — never assumed from
memory. AAPL and NVDA's splits are in the phase 2 ingestion scope already;
the reverse split (General Electric) and special dividend (Costco) are not —
see `tests/golden/price_events.json` and its own note on why a **test-only**
fixture list is used here rather than widening `config/ingest_scope_phase1.txt`.

**§4.2 automated jump detection.** `detect_jumps` flags every single-day
`adj_close` move beyond `JUMP_THRESHOLD`, annotated with whether it lines up
with a known corporate action or a nearby filing date — but never silently
dismissed on that basis. A human reviews the unexplained ones; the "or
earnings date" `TESTING.md` §2.2 mentions is approximated here by
`fundamental_facts.filed_date`, which is **not** the release date (a filing
typically lands days after the earnings release) — `near_filing_date` is
named to say "possibly earnings-related", not "confirmed".
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from shortlist.data.types import PriceBar

# TESTING.md §2.2: "single-day moves beyond ~35%".
JUMP_THRESHOLD = Decimal("0.35")

# A known corporate action can itself cause a same-day move — a special
# dividend is a real, if smaller, one-day drop. This is deliberately much
# smaller than JUMP_THRESHOLD: its job is to catch a botched split (a 4:1
# split gone wrong reads as a ~75% jump), not to certify every possible event.
_KNOWN_EVENT_DISCONTINUITY_TOLERANCE = Decimal("0.10")

# How close (in calendar days) a jump's date must be to a filed_date to be
# annotated as "possibly earnings" — a blunt proxy, not the release date
# itself. See module docstring.
DEFAULT_FILING_PROXIMITY_DAYS = 5


@dataclass(frozen=True, slots=True)
class PriceJump:
    """One single-day `adj_close` move beyond `JUMP_THRESHOLD`.

    Both `matched_known_event` and `near_filing_date` are informational, never
    a reason this jump was excluded from the result — `detect_jumps` returns
    every qualifying jump so a human decides what's explained.
    """

    ticker: str
    date: dt.date
    previous_date: dt.date
    previous_close: Decimal
    close: Decimal
    pct_change: Decimal
    matched_known_event: bool
    near_filing_date: bool


def detect_jumps(
    bars: Sequence[PriceBar],
    *,
    known_event_dates: frozenset[dt.date] = frozenset(),
    filing_dates: frozenset[dt.date] = frozenset(),
    threshold: Decimal = JUMP_THRESHOLD,
    filing_proximity_days: int = DEFAULT_FILING_PROXIMITY_DAYS,
) -> tuple[PriceJump, ...]:
    """Single-day `adj_close` moves beyond `threshold`, for one ticker's bars.

    `bars` need not be pre-sorted — sorted internally by date, since a jump is
    defined relative to the immediately preceding trading day, and callers
    (a repository's `get_bars`) already return sorted results but a test
    fixture may not.
    """
    sorted_bars = sorted(bars, key=lambda b: b.date)
    jumps: list[PriceJump] = []
    for previous, current in itertools.pairwise(sorted_bars):
        if previous.adj_close == 0:
            continue  # a zero prior close makes pct_change undefined, not infinite
        pct_change = (current.adj_close - previous.adj_close) / previous.adj_close
        if abs(pct_change) < threshold:
            continue
        near_filing = any(
            abs((current.date - filed).days) <= filing_proximity_days for filed in filing_dates
        )
        jumps.append(
            PriceJump(
                ticker=current.ticker,
                date=current.date,
                previous_date=previous.date,
                previous_close=previous.adj_close,
                close=current.adj_close,
                pct_change=pct_change,
                matched_known_event=current.date in known_event_dates,
                near_filing_date=near_filing,
            )
        )
    return tuple(jumps)


@dataclass(frozen=True, slots=True)
class KnownEvent:
    """One hand-verified corporate action, for the §4.1 known-action test.

    `description` records what was verified and against what, per PHASE_2.md
    §4.1: "confirm the actual split ratio and date from a primary source
    before hardcoding."
    """

    ticker: str
    event_date: dt.date
    description: str


# Verified live against the provider's own events feed, 2026-09-13 — see
# docs/phases/PHASE_2_NOTES.md §1. Not from memory or documentation.
KNOWN_EVENTS: tuple[KnownEvent, ...] = (
    KnownEvent("AAPL", dt.date(2020, 8, 31), "Apple 4:1 split"),
    KnownEvent("NVDA", dt.date(2021, 7, 20), "Nvidia 4:1 split"),
    KnownEvent("NVDA", dt.date(2024, 6, 10), "Nvidia 10:1 split"),
)


@dataclass(frozen=True, slots=True)
class KnownEventViolation:
    """A known corporate action's date shows a bigger discontinuity than a
    real, correctly-adjusted series should — PHASE_2.md §4.1's gate: "Assert
    the adjusted series shows no discontinuity across each event date."
    """

    event: KnownEvent
    pct_change: Decimal


def check_known_events(
    bars_by_ticker: Mapping[str, Sequence[PriceBar]],
    events: Sequence[KnownEvent] = KNOWN_EVENTS,
    *,
    tolerance: Decimal = _KNOWN_EVENT_DISCONTINUITY_TOLERANCE,
) -> tuple[KnownEventViolation, ...]:
    """For each `event`, assert the adjusted series has no large discontinuity
    on that date. A missing ticker or a missing bar either side of the event
    date is **not** a violation of this check — it means the data isn't there
    to check, which is a coverage question, not an adjustment-correctness one.
    """
    violations: list[KnownEventViolation] = []
    for event in events:
        bars = bars_by_ticker.get(event.ticker)
        if not bars:
            continue
        sorted_bars = sorted(bars, key=lambda b: b.date)
        on_or_after = [b for b in sorted_bars if b.date >= event.event_date]
        before = [b for b in sorted_bars if b.date < event.event_date]
        if not on_or_after or not before:
            continue
        previous = before[-1]
        current = on_or_after[0]
        if previous.adj_close == 0:
            continue
        pct_change = (current.adj_close - previous.adj_close) / previous.adj_close
        if abs(pct_change) > tolerance:
            violations.append(KnownEventViolation(event=event, pct_change=pct_change))
    return tuple(violations)
