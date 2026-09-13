"""Ticker <-> CIK resolution from `company_tickers.json`.

PHASE_1.md §4: "Do not trust the CIKs listed here; resolve them from
company_tickers.json and confirm." This module is that resolution step.

Ticker -> CIK is treated as **not injective over time**: tickers are reused
after a delisting (a new, unrelated company can list under an old symbol years
later), so `Cik` is the durable identifier and a ticker is only ever a label at
a point in time. `TESTING.md` §1.1 requires a delisted-company fixture to
remain retrievable — that only works if CIK, not ticker, is the join key
everywhere ingestion touches storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shortlist.data.types import Cik


@dataclass(frozen=True, slots=True)
class TickerRecord:
    """One `company_tickers.json` entry: a ticker as SEC currently has it mapped,
    with the company's display name for human review.
    """

    cik: Cik
    ticker: str
    title: str


def parse_company_tickers(payload: dict[str, Any]) -> tuple[TickerRecord, ...]:
    """Parse the `company_tickers.json` payload.

    SEC's format is `{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple
    Inc."}, "1": {...}, ...}` — an object keyed by an arbitrary numeric string,
    not an array. Order is not meaningful and is not preserved here.
    """
    records = []
    for entry in payload.values():
        cik = Cik.parse(entry["cik_str"])
        records.append(TickerRecord(cik=cik, ticker=entry["ticker"], title=entry["title"]))
    return tuple(records)


class TickerDirectory:
    """A resolved ticker <-> CIK mapping for one point in time.

    Ticker -> CIK is a lookup of convenience only (for turning a human-supplied
    symbol like "AAPL" into the CIK that everything downstream actually keys
    on); it is never assumed unique across history, only within this snapshot.
    """

    def __init__(self, records: tuple[TickerRecord, ...]) -> None:
        self._by_ticker: dict[str, TickerRecord] = {r.ticker: r for r in records}
        self._by_cik: dict[Cik, TickerRecord] = {r.cik: r for r in records}

    def cik_for_ticker(self, ticker: str) -> Cik | None:
        record = self._by_ticker.get(ticker.upper())
        return record.cik if record is not None else None

    def ticker_for_cik(self, cik: Cik) -> str | None:
        record = self._by_cik.get(cik)
        return record.ticker if record is not None else None

    def __len__(self) -> int:
        return len(self._by_cik)
