"""Ticker resolution and date bounding for price ingestion — PHASE_2.md §0.1.

Price providers are queried by ticker; everything else in this system is keyed
by CIK; and a ticker can be reassigned to an unrelated company years after a
delisting. `PHASE_2.md` §0.1's chosen resolution keeps `PriceRepository`/
`PriceReader` ticker-keyed (phase 0's tested interface is untouched) and instead
makes ingestion responsible for two things: knowing which ticker a CIK traded
under, and never fetching outside the date range that CIK actually held it.

**This module's date bounding is a stopgap, not a final design — see
`docs/phases/PHASE_2_NOTES.md` §0.2.** `DESIGN.md` §3.3's real resolution chain
(`company_tickers.json` → `formerNames`/`submissions` → manual override) cannot
supply ticker-validity date ranges: steps 1 and 3 are built, step 2 is phase
3's work, and `formerNames` records *name* history, not ticker history, so it
would not answer this question even once built. In its place, this module
derives a CIK's valid window from its own `fundamental_facts` filing activity —
`[min(filed_date), max(filed_date) + grace]` — which approximates but does not
equal the true trading-date range. Replace this with phase 3's time-aware
`sector_membership`/`universe_snapshots` windows once they exist; do not treat
this proxy as settled merely because it already works.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from shortlist.data.repository import FactRepository
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from shortlist.ingest.scope import PHASE_1_CIK_OVERRIDES
from shortlist.ingest.tickers import TickerDirectory

# A company's last filing typically precedes its final trading day (e.g. an
# acquisition closes weeks after the last 10-Q), and filed_date says nothing
# about trading before a company's first filing either. This margin is a
# blunt compensation for the first case only — see the module docstring and
# docs/phases/PHASE_2_NOTES.md §0.2 for why it is approximate, not precise.
DEFAULT_GRACE_DAYS = 120


class TickerNotResolvedError(RuntimeError):
    """A CIK in scope has no known ticker.

    Mirrors `scope.UnresolvedTickerError`'s reasoning: neither
    `company_tickers.json` nor `PHASE_1_CIK_OVERRIDES` names a ticker for this
    CIK, and guessing one would risk fetching an unrelated company's prices
    under a name that happens to look plausible.
    """

    def __init__(self, cik: Cik) -> None:
        self.cik = cik
        super().__init__(
            f"No ticker known for {cik}. Add an entry to PHASE_1_CIK_OVERRIDES "
            "(shortlist.ingest.scope) or confirm it resolves via "
            "company_tickers.json before adding it to price ingestion scope."
        )


class NoFilingActivityError(RuntimeError):
    """A CIK in scope has no `fundamental_facts` rows as of the given date, so
    no date window can be derived by this module's (stopgap) method.

    Raised rather than defaulted to an arbitrary wide window — an empty
    filing history is unexpected for any company that passed through phase
    1's ingestion, so it is worth surfacing rather than silently working
    around.
    """

    def __init__(self, cik: Cik) -> None:
        self.cik = cik
        super().__init__(
            f"No fundamental_facts rows found for {cik}; cannot derive a "
            "ticker-validity window. Confirm phase 1 ingestion has run for "
            "this CIK before requesting its price history."
        )


@dataclass(frozen=True, slots=True)
class TickerWindow:
    """The ticker a CIK traded under, and the date range price fetches for it
    are bounded to. `valid_to` is capped at `as_of` — this window describes
    what is fetchable as of a given date, not a company's entire future.
    """

    cik: Cik
    ticker: str
    valid_from: date
    valid_to: date


def _reverse_overrides() -> dict[Cik, str]:
    """`PHASE_1_CIK_OVERRIDES` maps ticker -> CIK (scope.py's direction); this
    module needs the reverse. Built fresh each call rather than cached at
    import time — the mapping is small and constant, and caching a module
    against a constant it doesn't own would be needless coupling.
    """
    return {cik: ticker for ticker, cik in PHASE_1_CIK_OVERRIDES.items()}


def ticker_for_cik(cik: Cik, directory: TickerDirectory) -> str:
    """The ticker `cik` is known to trade or have traded under.

    Tries the live directory first (the common case — a currently-listed
    company), then `PHASE_1_CIK_OVERRIDES` (the delisted/acquired case phase 1
    already hand-verified). Raises rather than guessing when neither knows it.
    """
    ticker = directory.ticker_for_cik(cik)
    if ticker is not None:
        return ticker
    ticker = _reverse_overrides().get(cik)
    if ticker is not None:
        return ticker
    raise TickerNotResolvedError(cik)


def ticker_windows(
    ciks: list[Cik],
    directory: TickerDirectory,
    fact_repo: FactRepository,
    as_of: AsOfDate,
    *,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> tuple[TickerWindow, ...]:
    """Resolve each CIK's ticker and its bounded, fetchable date window.

    Reads through the **guarded** `FactRepository` — `as_of` is explicit, per
    phase 0's "no default 'now'" rule, and the point-in-time invariant applies
    to this derivation exactly as it does to every other read.
    """
    concepts = list(CanonicalConcept)
    windows: list[TickerWindow] = []
    for cik in ciks:
        ticker = ticker_for_cik(cik, directory)
        facts = fact_repo.get_facts_for_universe([cik], concepts, as_of)
        if not facts:
            raise NoFilingActivityError(cik)
        filed_dates = [f.filed_date for f in facts]
        valid_from = min(filed_dates)
        valid_to = min(max(filed_dates) + timedelta(days=grace_days), as_of.value)
        windows.append(
            TickerWindow(cik=cik, ticker=ticker, valid_from=valid_from, valid_to=valid_to)
        )
    return tuple(windows)
