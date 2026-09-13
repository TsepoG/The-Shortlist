"""Shared scope resolution for the phase 1 and phase 2 CLI scripts.

Extracted from `run_phase1.py` when `run_phase2.py` needed the identical
logic — PHASE_2.md §3 is explicit: "reuse phase 1's `load_scope`/
`PHASE_1_CIK_OVERRIDES` pattern for the same nine companies — do not build a
second scope mechanism." Both scripts import from here rather than each
maintaining its own copy of the ticker-cache / scope-resolution glue.
"""

from __future__ import annotations

import json
from pathlib import Path

from shortlist.data.types import Cik
from shortlist.ingest.edgar_client import EdgarClient
from shortlist.ingest.scope import load_scope
from shortlist.ingest.tickers import TickerDirectory, parse_company_tickers

DATA_DIR = Path("data")
TICKERS_CACHE_PATH = DATA_DIR / "company_tickers.json"
SCOPE_PATH = Path("config/ingest_scope_phase1.txt")


def cached_ticker_directory() -> TickerDirectory:
    """`company_tickers.json`, fetched once and cached to disk. Every later
    run reuses the cache and makes no network call for it at all.
    """
    if TICKERS_CACHE_PATH.exists():
        payload = json.loads(TICKERS_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        with EdgarClient() as client:
            payload = client.get_company_tickers()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TICKERS_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return TickerDirectory(parse_company_tickers(payload))


def resolve_scope() -> tuple[Cik, ...]:
    """The nine phase 1/2 companies, resolved via `load_scope`'s default
    overrides — never a re-typed CIK list.
    """
    directory = cached_ticker_directory()
    return load_scope(SCOPE_PATH, directory)
