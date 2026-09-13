"""Unit tests for ticker resolution and date bounding — PHASE_2.md §0.1.

No network, no database: `guarded_fact_repo` (the same in-memory fake phase 0
built) stands in for `FactRepository`.
"""

from __future__ import annotations

from datetime import date

import pytest

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik
from shortlist.ingest.prices.resolution import (
    NoFilingActivityError,
    TickerNotResolvedError,
    ticker_for_cik,
    ticker_windows,
)
from shortlist.ingest.tickers import TickerDirectory, TickerRecord
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

AAPL_CIK = Cik.parse("0000320193")
XLNX_CIK = Cik.parse("0000743988")
UNKNOWN_CIK = Cik.parse("0000000001")

AS_OF = AsOfDate.parse("2026-01-01")


def _directory(*records: TickerRecord) -> TickerDirectory:
    return TickerDirectory(records)


# --- ticker_for_cik ------------------------------------------------------


def test_resolves_via_live_directory() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))

    assert ticker_for_cik(AAPL_CIK, directory) == "AAPL"


def test_resolves_delisted_cik_via_phase_1_overrides() -> None:
    # XLNX is absent from company_tickers.json (confirmed against the live
    # file during planning) but present in PHASE_1_CIK_OVERRIDES.
    directory = _directory()  # empty — simulates a delisted company

    assert ticker_for_cik(XLNX_CIK, directory) == "XLNX"


def test_raises_when_neither_source_knows_the_cik() -> None:
    directory = _directory()

    with pytest.raises(TickerNotResolvedError):
        ticker_for_cik(UNKNOWN_CIK, directory)


# --- ticker_windows --------------------------------------------------------


def test_window_spans_min_to_max_filed_date_plus_grace() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = guarded_fact_repo(
        fact(
            cik=AAPL_CIK,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        ),
        fact(
            cik=AAPL_CIK,
            concept=CanonicalConcept.NET_INCOME,
            value=50,
            period_end="2015-12-31",
            filed_date="2016-02-10",
        ),
    )

    windows = ticker_windows([AAPL_CIK], directory, repo, AS_OF, grace_days=30)

    assert len(windows) == 1
    w = windows[0]
    assert w.cik == AAPL_CIK
    assert w.ticker == "AAPL"
    assert w.valid_from == date(2015, 2, 15)
    assert w.valid_to == date(2016, 3, 11)  # 2016-02-10 + 30 days


def test_valid_to_is_capped_at_as_of() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = guarded_fact_repo(
        fact(
            cik=AAPL_CIK,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2025-12-31",
            filed_date="2025-12-20",
        ),
    )
    as_of = AsOfDate.parse("2026-01-01")

    windows = ticker_windows([AAPL_CIK], directory, repo, as_of, grace_days=120)

    # 2025-12-20 + 120 days would be well past 2026-01-01; must be clamped.
    assert windows[0].valid_to == date(2026, 1, 1)


def test_raises_when_cik_has_no_filing_activity() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = guarded_fact_repo()  # empty

    with pytest.raises(NoFilingActivityError):
        ticker_windows([AAPL_CIK], directory, repo, AS_OF)


def test_multiple_ciks_resolve_independently() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = guarded_fact_repo(
        fact(
            cik=AAPL_CIK,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        ),
        fact(
            cik=XLNX_CIK,
            concept=CanonicalConcept.REVENUE,
            value=200,
            period_end="2020-03-31",
            filed_date="2020-05-01",
        ),
    )

    windows = ticker_windows([AAPL_CIK, XLNX_CIK], directory, repo, AS_OF)

    by_cik = {w.cik: w for w in windows}
    assert by_cik[AAPL_CIK].ticker == "AAPL"
    assert by_cik[XLNX_CIK].ticker == "XLNX"


def test_window_never_extends_past_asof_via_guard() -> None:
    # A fact filed after as_of must never leak into this calculation — proves
    # ticker_windows goes through the real guarded repository, not a raw one.
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = guarded_fact_repo(
        fact(
            cik=AAPL_CIK,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date="2015-02-15",
        ),
    )
    early_as_of = AsOfDate.parse("2015-01-01")  # before the only fact's filed_date

    with pytest.raises(NoFilingActivityError):
        ticker_windows([AAPL_CIK], directory, repo, early_as_of)
