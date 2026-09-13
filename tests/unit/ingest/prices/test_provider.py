"""Unit tests for the price provider — no network.

`parse_chart_payload` is tested directly against constructed JSON (the same
split phase 1 used for `parse_companyfacts`); `YahooPriceProvider` is tested
via `httpx.MockTransport`, mirroring `test_edgar_client.py`'s pattern.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from shortlist.config import MissingConfigError
from shortlist.ingest.prices.provider import (
    DIVIDEND,
    SPLIT,
    PriceProviderError,
    YahooPriceProvider,
    parse_chart_payload,
)


@pytest.fixture(autouse=True)
def _user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTLIST_PRICE_USER_AGENT", "Shortlist tests <test@example.com>")


def _epoch(d: date) -> int:
    import datetime as dt

    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp())


def _chart_payload(
    *,
    timestamps: list[date],
    close: list[float | None],
    adjclose: list[float | None] | None = None,
    open_: list[float | None] | None = None,
    splits: dict[str, dict[str, object]] | None = None,
    dividends: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    quote: dict[str, object] = {"close": close}
    if open_ is not None:
        quote["open"] = open_
    result: dict[str, object] = {
        "timestamp": [_epoch(d) for d in timestamps],
        "indicators": {"quote": [quote]},
    }
    if adjclose is not None:
        result["indicators"]["adjclose"] = [{"adjclose": adjclose}]  # type: ignore[index]
    events: dict[str, object] = {}
    if splits is not None:
        events["splits"] = splits
    if dividends is not None:
        events["dividends"] = dividends
    if events:
        result["events"] = events
    return {"chart": {"result": [result], "error": None}}


# --- parse_chart_payload: bars -----------------------------------------------


def test_parses_bars_with_adjclose() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 6, 3), date(2024, 6, 4)],
        close=[115.00, 116.44],
        adjclose=[114.67, 116.11],
        open_=[114.0, 115.5],
    )

    bars, actions = parse_chart_payload(payload, date(2024, 6, 1), date(2024, 6, 30))

    assert len(bars) == 2
    assert bars[0].date == date(2024, 6, 3)
    assert bars[0].close == Decimal("115.0")
    assert bars[0].adj_close == Decimal("114.67")
    assert bars[0].open == Decimal("114.0")
    assert actions == ()


def test_falls_back_to_close_when_adjclose_block_absent() -> None:
    payload = _chart_payload(timestamps=[date(2024, 1, 2)], close=[100.0])

    bars, _ = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert len(bars) == 1
    assert bars[0].adj_close == Decimal("100.0")


def test_skips_a_bar_with_no_usable_close_at_all() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 1, 2), date(2024, 1, 3)],
        close=[None, 100.0],
        adjclose=[None, 99.5],
    )

    bars, _ = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert len(bars) == 1
    assert bars[0].date == date(2024, 1, 3)


def test_bars_outside_the_requested_range_are_excluded() -> None:
    # Yahoo's period1/period2 are approximate; the parser re-filters to the
    # exact requested window rather than trusting the provider's own bounds.
    payload = _chart_payload(
        timestamps=[date(2023, 12, 31), date(2024, 1, 15), date(2024, 2, 1)],
        close=[100.0, 101.0, 102.0],
        adjclose=[100.0, 101.0, 102.0],
    )

    bars, _ = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert [b.date for b in bars] == [date(2024, 1, 15)]


def test_bars_are_sorted_by_date() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 1, 3), date(2024, 1, 2)],
        close=[102.0, 101.0],
        adjclose=[102.0, 101.0],
    )

    bars, _ = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert [b.date for b in bars] == [date(2024, 1, 2), date(2024, 1, 3)]


# --- parse_chart_payload: corporate actions -----------------------------------


def test_parses_a_split_as_a_multiplicative_factor() -> None:
    # A 10:1 split — numerator 10, denominator 1 — must be stored as the
    # factor 10, not the string "10:1" or its reciprocal.
    payload = _chart_payload(
        timestamps=[date(2024, 6, 10)],
        close=[121.79],
        splits={
            "1718026200": {
                "date": _epoch(date(2024, 6, 10)),
                "numerator": 10.0,
                "denominator": 1.0,
                "splitRatio": "10:1",
            }
        },
    )

    _, actions = parse_chart_payload(payload, date(2024, 6, 1), date(2024, 6, 30))

    assert len(actions) == 1
    assert actions[0].event_type == SPLIT
    assert actions[0].event_date == date(2024, 6, 10)
    assert actions[0].ratio_or_amount == Decimal("10")


def test_parses_a_dividend_as_its_cash_amount() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 6, 11)],
        close=[120.91],
        dividends={"1718112600": {"date": _epoch(date(2024, 6, 11)), "amount": 0.01}},
    )

    _, actions = parse_chart_payload(payload, date(2024, 6, 1), date(2024, 6, 30))

    assert len(actions) == 1
    assert actions[0].event_type == DIVIDEND
    assert actions[0].ratio_or_amount == Decimal("0.01")


def test_a_split_with_zero_denominator_is_skipped_not_a_zero_division() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 1, 2)],
        close=[100.0],
        splits={"x": {"date": _epoch(date(2024, 1, 2)), "numerator": 10.0, "denominator": 0.0}},
    )

    _, actions = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert actions == ()


def test_actions_outside_the_requested_range_are_excluded() -> None:
    payload = _chart_payload(
        timestamps=[date(2024, 1, 2)],
        close=[100.0],
        splits={"x": {"date": _epoch(date(2023, 1, 1)), "numerator": 2.0, "denominator": 1.0}},
    )

    _, actions = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert actions == ()


# --- parse_chart_payload: error handling --------------------------------------


def test_raises_on_provider_reported_error() -> None:
    payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "no data"}}}

    with pytest.raises(PriceProviderError, match="Not Found"):
        parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))


def test_missing_chart_key_raises() -> None:
    with pytest.raises(PriceProviderError, match="chart"):
        parse_chart_payload({}, date(2024, 1, 1), date(2024, 1, 31))


def test_empty_result_list_returns_empty_series() -> None:
    payload: dict[str, object] = {"chart": {"result": [], "error": None}}

    bars, actions = parse_chart_payload(payload, date(2024, 1, 1), date(2024, 1, 31))

    assert bars == ()
    assert actions == ()


# --- YahooPriceProvider: transport-level behaviour ----------------------------


def test_provider_construction_requires_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHORTLIST_PRICE_USER_AGENT", raising=False)

    with pytest.raises(MissingConfigError):
        YahooPriceProvider()


def test_provider_sends_configured_user_agent() -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json=_chart_payload(timestamps=[], close=[]))

    with YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider:
        provider.get_history("AAPL", date(2024, 1, 1), date(2024, 1, 31))

    assert seen_headers == ["Shortlist tests <test@example.com>"]


def test_provider_returns_empty_series_on_404_without_raising() -> None:
    # A delisted ticker (e.g. XLNX) is a legitimate, reportable "no data" —
    # never an exception.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider:
        bars, actions = provider.get_history("XLNX", date(2024, 1, 1), date(2024, 1, 31))

    assert bars == ()
    assert actions == ()


def test_provider_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shortlist.ingest.prices.provider.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json=_chart_payload(timestamps=[], close=[]))

    with YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider:
        bars, actions = provider.get_history("AAPL", date(2024, 1, 1), date(2024, 1, 31))

    assert attempts["count"] == 3
    assert bars == ()
    assert actions == ()


def test_provider_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shortlist.ingest.prices.provider.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with (
        YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider,
        pytest.raises(PriceProviderError),
    ):
        provider.get_history("AAPL", date(2024, 1, 1), date(2024, 1, 31))


def test_provider_raises_on_unexpected_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418)

    with (
        YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider,
        pytest.raises(PriceProviderError),
    ):
        provider.get_history("AAPL", date(2024, 1, 1), date(2024, 1, 31))


def test_provider_rejects_start_after_end() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with (
        YahooPriceProvider(transport=transport) as provider,
        pytest.raises(ValueError, match="after"),
    ):
        provider.get_history("AAPL", date(2024, 2, 1), date(2024, 1, 1))


def test_provider_end_to_end_returns_parsed_bars_and_actions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chart_payload(
                timestamps=[date(2024, 6, 10)],
                close=[121.79],
                adjclose=[121.44],
                splits={
                    "x": {
                        "date": _epoch(date(2024, 6, 10)),
                        "numerator": 10.0,
                        "denominator": 1.0,
                    }
                },
            ),
        )

    with YahooPriceProvider(transport=httpx.MockTransport(handler)) as provider:
        bars, actions = provider.get_history("NVDA", date(2024, 6, 1), date(2024, 6, 30))

    assert len(bars) == 1 and bars[0].adj_close == Decimal("121.44")
    assert len(actions) == 1 and actions[0].ratio_or_amount == Decimal("10")
