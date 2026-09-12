"""Loads the phase 1 ingestion scope file (`config/ingest_scope_phase1.txt`).

See that file's header and `docs/phases/PHASE_1_NOTES.md` for why this exists:
`sector_membership` (phase 3) is the real, curated sector definition; this is
an explicit, reviewable stand-in so phase 1's backfill has a bounded, named
scope rather than silently ingesting everything or reaching for a SIC-code
filter `DESIGN.md` §3.3 already rejects as too coarse.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from shortlist.data.types import Cik
from shortlist.ingest.tickers import TickerDirectory

PHASE_1_CIK_OVERRIDES: Mapping[str, Cik] = {
    # Hand-verified 2026-09-12 against SEC's own endpoints only — EDGAR company
    # search / full-text search for the CIK, then that CIK's companyfacts
    # entityName and a revenue figure to confirm it is the right company. Each
    # one's filed_date range also terminates at its acquisition. See
    # docs/phases/PHASE_1_NOTES.md §2 for the full verification record.
    "XLNX": Cik.parse(743988),  # XILINX, INC. — acquired by AMD, 2022
    "MXIM": Cik.parse(743316),  # MAXIM INTEGRATED PRODUCTS, INC. — ADI, 2021
    "CY": Cik.parse(791915),  # Cypress Semiconductor Corporation — Infineon, 2020
    "MLNX": Cik.parse(1356104),  # Mellanox Technologies, Ltd. — Nvidia, 2020
}


class UnresolvedTickerError(RuntimeError):
    """A scope-file ticker did not resolve via `company_tickers.json`.

    Most often this means the ticker belongs to a delisted or acquired company
    that the current tickers file no longer lists — see the scope file's own
    header comment. The fix is a manual, hand-verified CIK override, added to
    `PHASE_1_CIK_OVERRIDES` (or supplied ad hoc via the `overrides` argument)
    — never a guess baked into the loader.
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(
            f"Ticker {ticker!r} did not resolve via company_tickers.json. "
            "If this is a delisted/acquired company, resolve its CIK by hand from "
            "EDGAR's own company search and pass it via the `overrides` argument."
        )


def parse_scope_tickers(text: str) -> tuple[str, ...]:
    """Extract the ordered, de-duplicated list of tickers from scope-file text.

    Pure parsing, no I/O and no ticker resolution — kept separate from
    `load_scope` so the file format itself is testable without a
    `TickerDirectory`.
    """
    tickers: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        ticker = line.upper()
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tuple(tickers)


def load_scope(
    path: Path,
    directory: TickerDirectory,
    *,
    overrides: Mapping[str, Cik] | None = None,
) -> tuple[Cik, ...]:
    """Resolve every ticker in the scope file at `path` to a `Cik`.

    `overrides` supplies a hand-verified CIK for a ticker that
    `company_tickers.json` doesn't resolve (typically a delisted company) —
    see `UnresolvedTickerError`. Defaults to `PHASE_1_CIK_OVERRIDES`; pass an
    explicit mapping (including `{}`) to replace that default rather than add
    to it.
    """
    overrides = PHASE_1_CIK_OVERRIDES if overrides is None else overrides
    tickers = parse_scope_tickers(path.read_text(encoding="utf-8"))

    ciks: list[Cik] = []
    for ticker in tickers:
        if ticker in overrides:
            ciks.append(overrides[ticker])
            continue
        cik = directory.cik_for_ticker(ticker)
        if cik is None:
            raise UnresolvedTickerError(ticker)
        ciks.append(cik)
    return tuple(ciks)
