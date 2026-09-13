"""The price-provider seam — PHASE_2.md §1.

`DESIGN.md` §4.1: "Write the loader behind a provider interface so a second
source can be added later as a cross-check (`TESTING.md` §2.3) without touching
callers." `PriceProvider` is that interface. Everything above it works in
`RawBar`/`RawCorporateAction`, never in a provider's own response shape.

**Provider selection changed during this phase, with cause.** `DESIGN.md` §7a
settled on Stooq ("free, no API key"). Verified live 2026-09-13 that Stooq now
sits behind a JavaScript proof-of-work anti-bot challenge: a plain HTTP client
receives 404 without a browser `User-Agent`, and with one receives a SHA-256
grinding challenge posting to `/__verify` rather than CSV. Using it would mean
deliberately circumventing an access control its operator installed on purpose.
Yahoo's chart endpoint was adopted instead — and turns out to fit `PHASE_2.md`
§2.2's "store the adjustment factor, not just its already-applied effect"
better than a bare Stooq CSV would have, since it returns split ratios and
dividend amounts as explicit factors. See `docs/phases/PHASE_2_NOTES.md` §1.

**The adjustment convention was verified, not assumed** (PHASE_2.md §1: "Do not
assume Stooq's adjustment behavior from memory or documentation"). What the
real responses show is in `YahooPriceProvider`'s docstring — it is not what the
spec's step-4 wording anticipated.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import httpx

from shortlist.config import price_user_agent

SPLIT = "split"
DIVIDEND = "dividend"


class PriceProviderError(RuntimeError):
    """A provider could not return usable data for a request.

    Distinct from an empty result: an unknown ticker legitimately has no bars
    (see `PriceProvider.get_history`), whereas this signals the request itself
    failed — a transport error, an unparseable body, or a response whose shape
    the parser does not recognise. Never swallowed into an empty series, which
    would read downstream as "this company simply didn't trade".
    """


@dataclass(frozen=True, slots=True)
class RawBar:
    """One provider-reported daily bar, before any CIK attribution.

    Deliberately **not** a `PriceBar` (the phase 0 type): a `PriceBar` carries a
    `ticker` and is a storage/read concern, while this is transport. Keeping
    them separate is what lets `loader.py` own attribution — deciding which CIK
    a bar belongs to is not something a provider can know.

    Every OHLCV field except `adj_close` is optional because real responses
    contain nulls (a halted session, a gap in the provider's own history).
    `adj_close` is required: a bar without it cannot contribute to a trailing
    high, which is the one thing the dip screen actually reads.
    """

    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adj_close: Decimal
    volume: int | None


@dataclass(frozen=True, slots=True)
class RawCorporateAction:
    """One split or dividend as the provider reported it.

    `ratio_or_amount` is the split's multiplicative factor (a 10:1 split is
    `Decimal("10")`) or the dividend's per-share cash amount. Which of the two
    it means is determined by `event_type`; storing the factor rather than its
    already-applied effect is `PHASE_2.md` §2.2's explicit requirement.
    """

    event_date: date
    event_type: str
    ratio_or_amount: Decimal


@runtime_checkable
class PriceProvider(Protocol):
    """What `loader.py` needs from any price source.

    `name` is not decoration: it becomes `prices.source` /
    `corporate_actions.source`, which are part of those tables' unique keys, so
    two providers can legitimately disagree about the same (cik, date) without
    either overwriting the other — that disagreement is exactly what
    `TESTING.md` §2.3's cross-source check exists to surface.
    """

    name: str

    def get_history(
        self, ticker: str, start: date, end: date
    ) -> tuple[tuple[RawBar, ...], tuple[RawCorporateAction, ...]]:
        """Daily bars and corporate actions for `ticker` within `[start, end]`.

        Returns both in one call because a provider may deliver them in one
        response (Yahoo does); a provider needing two requests makes them
        internally rather than widening this interface.

        An unknown or delisted ticker returns `((), ())` rather than raising —
        absence of data is a legitimate, reportable outcome (four of phase 2's
        nine companies are in exactly this position), whereas a failed request
        raises `PriceProviderError`.
        """
        ...


# Yahoo's chart endpoint. Undocumented and unofficial — see
# docs/phases/PHASE_2_NOTES.md §0.3 on why that changes how the User-Agent
# here should be read, and §1.1 on why Stooq is not used.
_YAHOO_CHART_URL_TEMPLATE = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

_MAX_REQUESTS_PER_SECOND = 2  # conservative: no published limit, so back off by default
_MIN_INTERVAL_SECONDS = 1.0 / _MAX_REQUESTS_PER_SECOND
_MAX_RETRIES = 5
_INITIAL_BACKOFF_SECONDS = 1.0
_TIMEOUT_SECONDS = 30.0


def _to_decimal(value: object) -> Decimal | None:
    """Provider numbers arrive as JSON floats. Converting via `str` keeps the
    decimal representation the response actually carried, rather than the
    binary-float artefact `Decimal(float)` would produce — CLAUDE.md's
    "explicit Decimal for money ... never float equality".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value))
        except ArithmeticError:
            return None
    return None


def _date_to_epoch(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp())


def _epoch_to_date(value: object) -> date | None:
    """Yahoo timestamps are epoch seconds in exchange-local time-of-day (a
    16:00 close, or 09:30 for a split's effective date). Only the calendar day
    is kept — a `PriceBar` is a daily bar, and carrying a time would invite a
    timezone bug at the `date <= as_of` comparison the guard performs.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(float(value), tz=UTC).date()


class YahooPriceProvider:
    """`PriceProvider` against Yahoo's chart endpoint.

    **Verified adjustment convention (live, 2026-09-13 — NVDA across its
    2021-07-20 4:1 and 2024-06-10 10:1 splits):**

    - `indicators.quote[0].{open,high,low,close}` are **already split-adjusted**
      by the provider. NVDA's 2021-01-04 close is reported as `13.11`; the
      actually-traded price that day was ~$525, i.e. already divided by 40 for
      both subsequent splits. There is **no raw/unadjusted series available**.
    - `indicators.adjclose[0].adjclose` is split-adjusted **and**
      dividend-adjusted; for NVDA it differs from `close` by only ~1.003, which
      is the dividend component alone.

    Two consequences, both of which change what `PHASE_2.md` §3 step 4 can mean:

    1. We do **not** compute split adjustment — the provider already applied it.
       The pipeline verifies the provider's series against its own reported
       factors instead of applying those factors ourselves.
    2. The provider re-adjusts the whole series retroactively after each new
       split, so stored rows go stale. That is why `prices` is upserted rather
       than append-only — see `docs/phases/PHASE_2_NOTES.md` §0.1.

    One instance should be shared across an ingestion run so the rate limiter's
    clock is shared too, the same reasoning as `EdgarClient`.
    """

    name = "yahoo"

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"User-Agent": price_user_agent()}
        self._client = httpx.Client(headers=headers, timeout=_TIMEOUT_SECONDS, transport=transport)
        self._last_request_at: float = 0.0

    def __enter__(self) -> YahooPriceProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _MIN_INTERVAL_SECONDS:
            time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str, params: dict[str, str]) -> httpx.Response | None:
        """The response, or `None` for a definitive "no such ticker" (404).

        404 is not retried and not raised: a delisted ticker is exactly the
        case `get_history` documents as `((), ())`.
        """
        backoff = _INITIAL_BACKOFF_SECONDS
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise PriceProviderError(f"Request to {url} failed: {exc}") from exc
                time.sleep(backoff)
                backoff *= 2
                continue
            if response.status_code == 404:
                return None
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == _MAX_RETRIES - 1:
                    raise PriceProviderError(f"{response.status_code} from {url} after retries")
                time.sleep(backoff)
                backoff *= 2
                continue
            if response.status_code != 200:
                raise PriceProviderError(f"{response.status_code} from {url}")
            return response
        raise PriceProviderError(f"Exhausted retries for {url}")  # pragma: no cover

    def get_history(
        self, ticker: str, start: date, end: date
    ) -> tuple[tuple[RawBar, ...], tuple[RawCorporateAction, ...]]:
        if start > end:
            raise ValueError(f"start {start} is after end {end} for ticker {ticker!r}")
        seconds_per_day = 86400
        params = {
            "period1": str(_date_to_epoch(start)),
            # +1 day: Yahoo's period2 is exclusive of the instant, so an
            # inclusive calendar end date needs the next day's epoch.
            "period2": str(_date_to_epoch(end) + seconds_per_day),
            "interval": "1d",
            "events": "split,div",
        }
        response = self._get(_YAHOO_CHART_URL_TEMPLATE.format(ticker=ticker), params)
        if response is None:
            return (), ()
        try:
            payload = response.json()
        except ValueError as exc:
            raise PriceProviderError(f"Non-JSON response for ticker {ticker!r}") from exc
        return parse_chart_payload(payload, start, end)


def _parse_bars(result: dict[str, Any], start: date, end: date) -> tuple[RawBar, ...]:
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    quote = quotes[0] if quotes else {}
    adjclose_blocks = indicators.get("adjclose") or []
    adjclose = (adjclose_blocks[0] or {}).get("adjclose", []) if adjclose_blocks else []

    def at(series: Sequence[Any] | None, index: int) -> object:
        if series is None or index >= len(series):
            return None
        return series[index]

    bars: list[RawBar] = []
    for i, raw_timestamp in enumerate(timestamps):
        bar_date = _epoch_to_date(raw_timestamp)
        if bar_date is None or not (start <= bar_date <= end):
            continue
        close = _to_decimal(at(quote.get("close"), i))
        # adj_close is required; fall back to close only when the provider
        # omitted the adjclose block entirely (it does for some tickers).
        adj_close = _to_decimal(at(adjclose, i))
        if adj_close is None:
            adj_close = close
        if adj_close is None:
            continue  # no usable close at all — a genuinely empty session
        volume_raw = at(quote.get("volume"), i)
        bars.append(
            RawBar(
                date=bar_date,
                open=_to_decimal(at(quote.get("open"), i)),
                high=_to_decimal(at(quote.get("high"), i)),
                low=_to_decimal(at(quote.get("low"), i)),
                close=close,
                adj_close=adj_close,
                volume=int(volume_raw) if isinstance(volume_raw, int | float) else None,
            )
        )
    bars.sort(key=lambda b: b.date)
    return tuple(bars)


def _parse_actions(
    result: dict[str, Any], start: date, end: date
) -> tuple[RawCorporateAction, ...]:
    events = result.get("events") or {}
    actions: list[RawCorporateAction] = []

    for entry in (events.get("splits") or {}).values():
        event_date = _epoch_to_date(entry.get("date"))
        numerator = _to_decimal(entry.get("numerator"))
        denominator = _to_decimal(entry.get("denominator"))
        if event_date is None or numerator is None or not denominator:
            continue
        if not (start <= event_date <= end):
            continue
        # The multiplicative factor, not the "10:1" label: a 10:1 split means
        # every pre-split price divides by 10, and storing the factor is what
        # makes the adjustment auditable and recomputable (PHASE_2.md §2.2).
        actions.append(RawCorporateAction(event_date, SPLIT, numerator / denominator))

    for entry in (events.get("dividends") or {}).values():
        event_date = _epoch_to_date(entry.get("date"))
        amount = _to_decimal(entry.get("amount"))
        if event_date is None or amount is None:
            continue
        if not (start <= event_date <= end):
            continue
        actions.append(RawCorporateAction(event_date, DIVIDEND, amount))

    actions.sort(key=lambda a: (a.event_date, a.event_type))
    return tuple(actions)


def parse_chart_payload(
    payload: dict[str, Any], start: date, end: date
) -> tuple[tuple[RawBar, ...], tuple[RawCorporateAction, ...]]:
    """Parse a chart response into `(bars, actions)`.

    Split out from `YahooPriceProvider` so the parsing half is testable against
    recorded JSON with no network at all, the same split phase 1 used for
    `companyfacts.parse_companyfacts` — CLAUDE.md: "Unit tests must run without
    a database or network."
    """
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise PriceProviderError("Response has no 'chart' object")
    error = chart.get("error")
    if error:
        raise PriceProviderError(f"Provider reported an error: {error}")
    results = chart.get("result")
    if not results:
        return (), ()
    result = results[0]
    if not isinstance(result, dict):
        raise PriceProviderError("Response 'chart.result[0]' is not an object")
    return _parse_bars(result, start, end), _parse_actions(result, start, end)
