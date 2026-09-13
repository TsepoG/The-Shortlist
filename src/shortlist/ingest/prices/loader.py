"""Price ingestion orchestration — PHASE_2.md §3.

Composes:

- `resolution.ticker_windows` to bound each CIK's fetch to a defensible date
  range (PHASE_2.md §0.1);
- a `PriceProvider` to fetch raw bars and corporate actions;
- a `PriceWriter` (in practice `PostgresPriceWriter`, obtained via
  `shortlist.data.factory.create_price_writer`) to upsert them.

**What this module does NOT do, and why**: `PHASE_2.md` §3 step 4, as
originally worded, says to "compute `adj_close` ... by applying the full
cumulative adjustment factor multiplicatively across the whole series." This
module does not do that — verified against real data
(`src/shortlist/ingest/prices/provider.py`'s docstring; full record in
`docs/phases/PHASE_2_NOTES.md` §1) that the provider's `close`/`adj_close`
values already have every split applied, retroactively, across the entire
series. There is no raw series to compute an adjustment from.

What this module does instead: it **records** `split_factor_at_ingest` on
each stored row — the product of every split ratio still ahead of that bar's
date, i.e. exactly the retroactive adjustment the provider applied to it —
computed from the same response's own corporate-action events. That is what
makes a later re-ingestion's basis shift auditable (a bar's stored factor
changing between two ingestion runs is the signal that a new split occurred
and the whole series was re-based), rather than something this codebase
recomputes and could get wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from shortlist.data._backends.postgres import UpsertResult
from shortlist.data.repository import FactRepository
from shortlist.data.types import AsOfDate, Cik, CorporateActionRow, PriceRow
from shortlist.ingest.prices.provider import SPLIT, PriceProvider, RawBar, RawCorporateAction
from shortlist.ingest.prices.resolution import ticker_windows
from shortlist.ingest.tickers import TickerDirectory


@runtime_checkable
class PriceWriter(Protocol):
    """What `run_price_backfill` needs from a writer — matched structurally by
    `PostgresPriceWriter`, and by a fake in unit tests (no DB, no network).
    """

    def upsert_bars(self, rows: Sequence[PriceRow]) -> UpsertResult: ...

    def upsert_actions(self, rows: Sequence[CorporateActionRow]) -> UpsertResult: ...


@dataclass
class PriceBackfillSummary:
    """What a price backfill run did — for the quality-job artifacts
    (PHASE_2.md §4) and for confirming idempotency empirically (§8's row
    count + content hash).
    """

    companies_in_scope: int
    companies_processed: int = 0
    bars_inserted: int = 0
    bars_updated: int = 0
    actions_inserted: int = 0
    actions_updated: int = 0
    # Tickers whose provider fetch returned no data at all — recorded, never
    # silently absorbed. This is exactly the delisted-company gap
    # (XLNX/MXIM/CY/MLNX) docs/phases/PHASE_2_NOTES.md documents as blocking
    # phase 6, not phase 2's own gate.
    companies_with_no_data: list[str] = field(default_factory=list)


def _cumulative_split_factor(actions: Sequence[RawCorporateAction], bar_date: date) -> Decimal:
    """The product of every known split ratio whose `event_date` is strictly
    after `bar_date`.

    These are precisely the splits that had not yet happened as of `bar_date`
    and are therefore what caused the provider to retroactively divide this
    historical bar — verified empirically: NVDA's 2021-01-04 close is
    reported at roughly 1/40th its actual traded price that day, and 40 is
    exactly the product of the 2021-07-20 4:1 and 2024-06-10 10:1 splits, both
    of which postdate it. See `provider.py`'s `YahooPriceProvider` docstring.
    """
    factor = Decimal(1)
    for action in actions:
        if action.event_type == SPLIT and action.event_date > bar_date:
            factor *= action.ratio_or_amount
    return factor


def _approximate_adj_high(bar: RawBar) -> Decimal | None:
    """`bar.high`, carried onto the same adjustment basis as `bar.adj_close`.

    **Decided in `docs/phases/PHASE_2_NOTES.md` §4: intraday basis
    (`max(adj_high)`), not closing basis.** Confirmed before writing this
    (`docs/phases/PHASE_2_NOTES.md` §1): the provider exposes no independently
    adjusted high at all — only `close`/`adj_close` — even when explicitly
    asked for one (`includeAdjustedClose=true`). This function is therefore
    the standard approximation of applying one day's own close/adj_close
    adjustment ratio uniformly across that same day's `high`.

    **Stated assumption, not just in the notes**: this assumes the dividend
    adjustment embedded in `adj_close / close` applies uniformly *within* a
    trading day — a simplification, since the actual intraday timing of a
    dividend's ex-date effect isn't represented in daily bars at all. If a
    provider is ever added that reports a precisely adjusted high directly,
    prefer that value over this computation rather than assuming this
    approximation is good enough regardless of source — verify first, the
    same discipline this phase applied to the adjustment convention itself.

    Returns `None` when there's nothing to scale (`bar.high` missing) or
    nothing to scale it *with* (`bar.close` is `None` or zero) — the caller
    then falls back to `adj_close`, exactly like the other OHLC fields.
    """
    if bar.high is None or not bar.close:
        return None
    return bar.high * (bar.adj_close / bar.close)


def _to_price_row(
    bar: RawBar,
    *,
    cik: Cik,
    ticker: str,
    source: str,
    actions: Sequence[RawCorporateAction],
) -> PriceRow:
    return PriceRow(
        cik=cik,
        ticker=ticker,
        source=source,
        date=bar.date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        adj_close=bar.adj_close,
        adj_high=_approximate_adj_high(bar),
        split_factor_at_ingest=_cumulative_split_factor(actions, bar.date),
    )


def _to_action_row(
    action: RawCorporateAction, *, cik: Cik, ticker: str, source: str
) -> CorporateActionRow:
    return CorporateActionRow(
        cik=cik,
        ticker=ticker,
        source=source,
        event_date=action.event_date,
        event_type=action.event_type,
        ratio_or_amount=action.ratio_or_amount,
    )


def run_price_backfill(
    ciks: list[Cik],
    directory: TickerDirectory,
    fact_repo: FactRepository,
    provider: PriceProvider,
    writer: PriceWriter,
    as_of: AsOfDate,
) -> PriceBackfillSummary:
    """Ingest daily bars and corporate actions for every CIK in `ciks`.

    A CIK whose provider fetch returns no data at all (a delisted company the
    provider doesn't carry) is recorded in
    `PriceBackfillSummary.companies_with_no_data`, never silently skipped
    without a trace — PHASE_2.md §7's "ask before proceeding" list includes
    exactly this case, and it is why it's a *reported* summary field rather
    than a log line.
    """
    windows = ticker_windows(ciks, directory, fact_repo, as_of)
    summary = PriceBackfillSummary(companies_in_scope=len(windows))

    for window in windows:
        bars, actions = provider.get_history(window.ticker, window.valid_from, window.valid_to)
        summary.companies_processed += 1
        if not bars:
            summary.companies_with_no_data.append(window.ticker)
            continue

        price_rows = [
            _to_price_row(
                b, cik=window.cik, ticker=window.ticker, source=provider.name, actions=actions
            )
            for b in bars
        ]
        bar_result = writer.upsert_bars(price_rows)
        summary.bars_inserted += bar_result.inserted
        summary.bars_updated += bar_result.updated

        if actions:
            action_rows = [
                _to_action_row(a, cik=window.cik, ticker=window.ticker, source=provider.name)
                for a in actions
            ]
            action_result = writer.upsert_actions(action_rows)
            summary.actions_inserted += action_result.inserted
            summary.actions_updated += action_result.updated

    return summary
