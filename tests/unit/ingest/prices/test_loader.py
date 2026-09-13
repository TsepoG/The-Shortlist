"""Unit tests for price ingestion orchestration — no network, no database.

`_FakePriceProvider` and `_FakePriceWriter` stand in for the real provider and
`PostgresPriceWriter`, matching PHASE_2.md §5's "unit (no network, no
database)" requirement — the same style phase 1's `test_backfill.py` uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from shortlist.data._backends.postgres import UpsertResult
from shortlist.data.repository import FactRepository
from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, CorporateActionRow, PriceRow
from shortlist.ingest.prices.loader import _cumulative_split_factor, run_price_backfill
from shortlist.ingest.prices.provider import DIVIDEND, SPLIT, RawBar, RawCorporateAction
from shortlist.ingest.tickers import TickerDirectory, TickerRecord
from tests.fakes import guarded_fact_repo
from tests.fakes.builders import fact

AAPL_CIK = Cik.parse("0000320193")
XLNX_CIK = Cik.parse("0000743988")
AS_OF = AsOfDate.parse("2026-01-01")


def _bar(d: str, close: float = 100.0, adj_close: float | None = None) -> RawBar:
    c = Decimal(str(close))
    return RawBar(
        date=date.fromisoformat(d),
        open=c,
        high=c,
        low=c,
        close=c,
        adj_close=Decimal(str(adj_close)) if adj_close is not None else c,
        volume=1000,
    )


def _split(d: str, ratio: str) -> RawCorporateAction:
    return RawCorporateAction(
        event_date=date.fromisoformat(d), event_type=SPLIT, ratio_or_amount=Decimal(ratio)
    )


def _dividend(d: str, amount: str) -> RawCorporateAction:
    return RawCorporateAction(
        event_date=date.fromisoformat(d), event_type=DIVIDEND, ratio_or_amount=Decimal(amount)
    )


class _FakePriceProvider:
    """Returns a canned `(bars, actions)` response per ticker; `None` means
    "no data at all", matching the real provider's 404 contract."""

    name = "fake"

    def __init__(
        self, responses: dict[str, tuple[tuple[RawBar, ...], tuple[RawCorporateAction, ...]] | None]
    ) -> None:
        self._responses = responses

    def get_history(
        self, ticker: str, start: date, end: date
    ) -> tuple[tuple[RawBar, ...], tuple[RawCorporateAction, ...]]:
        response = self._responses.get(ticker)
        return response if response is not None else ((), ())


@dataclass
class _FakePriceWriter:
    bar_rows: list[PriceRow] = field(default_factory=list)
    action_rows: list[CorporateActionRow] = field(default_factory=list)

    def upsert_bars(self, rows: Sequence[PriceRow]) -> UpsertResult:
        self.bar_rows.extend(rows)
        return UpsertResult(inserted=len(rows), updated=0)

    def upsert_actions(self, rows: Sequence[CorporateActionRow]) -> UpsertResult:
        self.action_rows.extend(rows)
        return UpsertResult(inserted=len(rows), updated=0)


def _directory(*records: TickerRecord) -> TickerDirectory:
    return TickerDirectory(records)


def _fact_repo_for(cik: Cik, filed_date: str = "2015-02-15") -> FactRepository:
    return guarded_fact_repo(
        fact(
            cik=cik,
            concept=CanonicalConcept.REVENUE,
            value=100,
            period_end="2014-12-31",
            filed_date=filed_date,
        )
    )


# --- _cumulative_split_factor -------------------------------------------


def test_cumulative_factor_is_one_with_no_future_splits() -> None:
    actions = (_split("2020-01-01", "4"),)  # in the past relative to the bar

    assert _cumulative_split_factor(actions, date(2021, 1, 1)) == Decimal("1")


def test_cumulative_factor_multiplies_every_future_split() -> None:
    # Mirrors NVDA: a bar before both its 2021 4:1 and 2024 10:1 splits
    # should carry the product of both, verified empirically (provider.py).
    actions = (
        _split("2021-07-20", "4"),
        _split("2024-06-10", "10"),
    )

    assert _cumulative_split_factor(actions, date(2020, 1, 1)) == Decimal("40")


def test_cumulative_factor_ignores_dividends() -> None:
    actions = (_dividend("2021-07-20", "0.01"),)

    assert _cumulative_split_factor(actions, date(2020, 1, 1)) == Decimal("1")


def test_cumulative_factor_excludes_a_split_on_the_bar_date_itself() -> None:
    # Strictly-after only: a split ON this date has already taken effect by
    # end of day, so it should not double-count into this bar's factor.
    actions = (_split("2021-07-20", "4"),)

    assert _cumulative_split_factor(actions, date(2021, 7, 20)) == Decimal("1")


# --- run_price_backfill ---------------------------------------------------


def test_backfill_writes_bars_and_actions_for_an_available_ticker() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = _fact_repo_for(AAPL_CIK)
    provider = _FakePriceProvider(
        {"AAPL": ((_bar("2024-06-10", 121.79, 121.44),), (_split("2024-06-10", "10"),))}
    )
    writer = _FakePriceWriter()

    summary = run_price_backfill([AAPL_CIK], directory, repo, provider, writer, AS_OF)

    assert summary.companies_in_scope == 1
    assert summary.companies_processed == 1
    assert summary.bars_inserted == 1
    assert summary.actions_inserted == 1
    assert summary.companies_with_no_data == []
    assert len(writer.bar_rows) == 1
    assert writer.bar_rows[0].cik == AAPL_CIK
    assert writer.bar_rows[0].ticker == "AAPL"
    assert writer.bar_rows[0].adj_close == Decimal("121.44")


def test_backfill_records_a_ticker_with_no_data_rather_than_skipping_silently() -> None:
    # The delisted-company case: XLNX has fundamental_facts (phase 1 ran) but
    # no price data at all from this provider.
    directory = _directory()  # XLNX resolves via PHASE_1_CIK_OVERRIDES, not this
    repo = _fact_repo_for(XLNX_CIK, filed_date="2020-01-15")
    provider = _FakePriceProvider({})  # no entry for "XLNX" -> ((), ())
    writer = _FakePriceWriter()

    summary = run_price_backfill([XLNX_CIK], directory, repo, provider, writer, AS_OF)

    assert summary.companies_processed == 1
    assert summary.companies_with_no_data == ["XLNX"]
    assert summary.bars_inserted == 0
    assert writer.bar_rows == []


def test_backfill_computes_split_factor_from_the_same_response() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = _fact_repo_for(AAPL_CIK)
    provider = _FakePriceProvider(
        {
            "AAPL": (
                (_bar("2020-01-01", 10.0), _bar("2024-07-01", 100.0)),
                (_split("2024-06-10", "10"),),
            )
        }
    )
    writer = _FakePriceWriter()

    run_price_backfill([AAPL_CIK], directory, repo, provider, writer, AS_OF)

    by_date = {r.date: r for r in writer.bar_rows}
    # Bar before the split carries the factor; bar after does not.
    assert by_date[date(2020, 1, 1)].split_factor_at_ingest == Decimal("10")
    assert by_date[date(2024, 7, 1)].split_factor_at_ingest == Decimal("1")


def test_backfill_does_not_write_actions_when_none_are_returned() -> None:
    directory = _directory(TickerRecord(cik=AAPL_CIK, ticker="AAPL", title="Apple Inc."))
    repo = _fact_repo_for(AAPL_CIK)
    provider = _FakePriceProvider({"AAPL": ((_bar("2024-01-02"),), ())})
    writer = _FakePriceWriter()

    summary = run_price_backfill([AAPL_CIK], directory, repo, provider, writer, AS_OF)

    assert summary.actions_inserted == 0
    assert writer.action_rows == []


def test_backfill_processes_multiple_ciks_independently() -> None:
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
            period_end="2019-12-31",
            filed_date="2020-01-15",
        ),
    )
    provider = _FakePriceProvider({"AAPL": ((_bar("2024-01-02"),), ())})
    writer = _FakePriceWriter()

    summary = run_price_backfill([AAPL_CIK, XLNX_CIK], directory, repo, provider, writer, AS_OF)

    assert summary.companies_in_scope == 2
    assert summary.companies_processed == 2
    assert summary.bars_inserted == 1
    assert summary.companies_with_no_data == ["XLNX"]
