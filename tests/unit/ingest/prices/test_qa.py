"""Unit tests for price quality jobs — PHASE_2.md §4. No network, no database.

The reverse-split / special-dividend cases in `tests/golden/price_events.json`
are loaded and fed through `check_known_events` here as synthetic series
(continuous, matching what was verified live against the real provider — see
that file's own `verification` field) rather than by re-fetching live data in
a unit test.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from shortlist.data.types import PriceBar
from shortlist.ingest.prices.qa import (
    KNOWN_EVENTS,
    KnownEvent,
    check_known_events,
    detect_jumps,
)

GOLDEN_PRICE_EVENTS_PATH = Path(__file__).resolve().parents[3] / "golden" / "price_events.json"


def _bar(ticker: str, d: str, adj_close: str) -> PriceBar:
    value = Decimal(adj_close)
    return PriceBar(
        ticker=ticker,
        date=date.fromisoformat(d),
        open=value,
        high=value,
        low=value,
        close=value,
        adj_close=value,
        adj_high=value,
        volume=0,
    )


# --- detect_jumps ----------------------------------------------------------


def test_no_jump_below_threshold() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-01-02", "120")]  # +20%

    assert detect_jumps(bars) == ()


def test_jump_above_threshold_is_detected() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-01-02", "150")]  # +50%

    jumps = detect_jumps(bars)

    assert len(jumps) == 1
    assert jumps[0].date == date(2024, 1, 2)
    assert jumps[0].pct_change == Decimal("0.5")


def test_a_downward_jump_is_also_detected() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-01-02", "50")]  # -50%

    jumps = detect_jumps(bars)

    assert len(jumps) == 1
    assert jumps[0].pct_change == Decimal("-0.5")


def test_bars_need_not_be_pre_sorted() -> None:
    bars = [_bar("AAPL", "2024-01-02", "150"), _bar("AAPL", "2024-01-01", "100")]

    jumps = detect_jumps(bars)

    assert len(jumps) == 1
    assert jumps[0].previous_date == date(2024, 1, 1)


def test_zero_previous_close_does_not_raise() -> None:
    bars = [_bar("AAPL", "2024-01-01", "0"), _bar("AAPL", "2024-01-02", "100")]

    assert detect_jumps(bars) == ()  # undefined pct change from zero, not a crash


def test_jump_matching_a_known_event_date_is_still_returned_but_annotated() -> None:
    # A real jump beyond threshold is never silently dismissed just because it
    # coincides with a known event — only annotated.
    bars = [_bar("AAPL", "2020-08-28", "500"), _bar("AAPL", "2020-08-31", "125")]  # -75%: a 4:1

    jumps = detect_jumps(bars, known_event_dates=frozenset({date(2020, 8, 31)}))

    assert len(jumps) == 1
    assert jumps[0].matched_known_event is True


def test_jump_not_matching_any_known_event_is_unmatched() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-01-02", "150")]

    jumps = detect_jumps(bars, known_event_dates=frozenset({date(2020, 8, 31)}))

    assert jumps[0].matched_known_event is False


def test_jump_near_a_filing_date_is_annotated_not_dismissed() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-01-04", "150")]

    jumps = detect_jumps(bars, filing_dates=frozenset({date(2024, 1, 3)}), filing_proximity_days=5)

    assert len(jumps) == 1
    assert jumps[0].near_filing_date is True
    # Annotated, not excluded from the result:
    assert jumps[0].pct_change == Decimal("0.5")


def test_jump_far_from_any_filing_date_is_not_annotated() -> None:
    bars = [_bar("AAPL", "2024-01-01", "100"), _bar("AAPL", "2024-06-01", "150")]

    jumps = detect_jumps(bars, filing_dates=frozenset({date(2024, 1, 3)}), filing_proximity_days=5)

    assert jumps[0].near_filing_date is False


# --- check_known_events -----------------------------------------------------


def test_continuous_series_across_a_known_split_has_no_violation() -> None:
    # A correctly-adjusted 4:1 split produces NO discontinuity in adj_close —
    # unlike a raw/unadjusted series, which would show a ~75% drop.
    event = KnownEvent("AAPL", date(2020, 8, 31), "test split")
    bars = {
        "AAPL": [
            _bar("AAPL", "2020-08-28", "129.04"),
            _bar("AAPL", "2020-08-31", "129.10"),
        ]
    }

    assert check_known_events(bars, [event]) == ()


def test_a_real_discontinuity_at_a_known_event_date_is_flagged() -> None:
    # This is the failure mode PHASE_2.md §4.1 exists to catch: an unhandled
    # or wrongly-applied split reads as a huge, spurious "dip".
    event = KnownEvent("AAPL", date(2020, 8, 31), "test split")
    bars = {
        "AAPL": [
            _bar("AAPL", "2020-08-28", "500.00"),  # not divided by 4 -- the bug
            _bar("AAPL", "2020-08-31", "129.10"),
        ]
    }

    violations = check_known_events(bars, [event])

    assert len(violations) == 1
    assert violations[0].event == event


def test_missing_ticker_data_is_not_a_violation() -> None:
    event = KnownEvent("XLNX", date(2020, 8, 31), "no data for this ticker")

    assert check_known_events({}, [event]) == ()


def test_missing_bar_either_side_of_the_event_is_not_a_violation() -> None:
    event = KnownEvent("AAPL", date(2020, 8, 31), "test split")
    bars = {"AAPL": [_bar("AAPL", "2020-08-31", "129.10")]}  # nothing before it

    assert check_known_events(bars, [event]) == ()


def test_default_known_events_cover_apple_and_nvidia_splits() -> None:
    tickers = {e.ticker for e in KNOWN_EVENTS}
    assert tickers == {"AAPL", "NVDA"}
    assert len(KNOWN_EVENTS) == 3  # AAPL 4:1, NVDA 4:1, NVDA 10:1


# --- the test-only reverse-split / special-dividend fixture list -----------


def test_golden_price_events_file_reverse_split_is_continuous() -> None:
    payload = json.loads(GOLDEN_PRICE_EVENTS_PATH.read_text(encoding="utf-8"))
    rs = payload["reverse_split"]
    event = KnownEvent(rs["ticker"], date.fromisoformat(rs["event_date"]), rs["description"])
    # Synthetic bars matching what was verified live (see the file's own
    # "verification" field): continuous across the reverse split.
    bars = {
        rs["ticker"]: [
            _bar(rs["ticker"], "2021-07-30", "64.54"),
            _bar(rs["ticker"], "2021-08-02", "62.68"),
        ]
    }

    assert check_known_events(bars, [event]) == ()


def test_golden_price_events_file_special_dividend_is_continuous() -> None:
    payload = json.loads(GOLDEN_PRICE_EVENTS_PATH.read_text(encoding="utf-8"))
    sd = payload["special_dividend"]
    event = KnownEvent(sd["ticker"], date.fromisoformat(sd["event_date"]), sd["description"])
    bars = {
        sd["ticker"]: [
            _bar(sd["ticker"], "2020-11-30", "391.77"),
            _bar(sd["ticker"], "2020-12-01", "387.56"),
        ]
    }

    assert check_known_events(bars, [event]) == ()
