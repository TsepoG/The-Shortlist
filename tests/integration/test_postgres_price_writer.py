"""`PostgresPriceWriter` / `PostgresPriceRepository`, against real Postgres.

Two things phase 1's fact-writer tests don't need to cover, because prices are
upserted rather than append-only (`docs/phases/PHASE_2_NOTES.md` §0.1):

- **Idempotency is `inserted=0, updated=N`, not `inserted=0, skipped=N`** — an
  upsert always writes every row, either as a fresh insert or an update.
- **No stale-adjustment-basis mixing across a re-ingestion**
  (`PHASE_2.md` §4.3): re-upserting the same `(cik, date, source)` with a new
  `adj_close` (simulating what happens after a new split retroactively
  re-bases the whole series) must make `get_bars` return the NEW value, never
  the old one — proving the upsert actually replaces rather than coexists
  with a stale row.

Also covers `AmbiguousTickerError`: PHASE_2.md §0.1 keeps `get_bars`
ticker-keyed, so a genuine ticker collision between two CIKs must be a loud
failure here, at the one place with enough information (both `cik` and
`ticker` columns) to detect it.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from shortlist.data._backends.postgres import PostgresPriceRepository, PostgresPriceWriter
from shortlist.data._backends.schema import prices
from shortlist.data.guard import AmbiguousTickerError
from shortlist.data.types import AsOfDate, Cik, CorporateActionRow, PriceRow

pytestmark = pytest.mark.integration

_CIK_A = Cik.parse("0000000001")
_CIK_B = Cik.parse("0000000002")
_AS_OF = AsOfDate.parse("2026-01-01")


def _row(
    *,
    cik: Cik = _CIK_A,
    ticker: str = "TEST",
    source: str = "fake",
    d: str = "2024-01-02",
    adj_close: str = "100",
    split_factor: str = "1",
) -> PriceRow:
    value = Decimal(adj_close)
    return PriceRow(
        cik=cik,
        ticker=ticker,
        source=source,
        date=date.fromisoformat(d),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=1000,
        adj_close=value,
        adj_high=None,
        split_factor_at_ingest=Decimal(split_factor),
    )


def test_upsert_bars_reports_new_rows_as_inserted(pg_connection: Connection) -> None:
    writer = PostgresPriceWriter(pg_connection)

    result = writer.upsert_bars([_row(d="2024-01-02"), _row(d="2024-01-03")])

    assert result.inserted == 2
    assert result.updated == 0


def test_re_upserting_identical_rows_reports_updated_not_inserted(
    pg_connection: Connection,
) -> None:
    writer = PostgresPriceWriter(pg_connection)
    row = _row(d="2024-01-02")
    writer.upsert_bars([row])

    result = writer.upsert_bars([row])

    assert result.inserted == 0
    assert result.updated == 1


def test_re_upsert_replaces_stale_adjustment_not_coexists_with_it(
    pg_connection: Connection,
) -> None:
    # Simulates a new split occurring between two ingestion runs: the
    # provider retroactively re-bases the entire series, so the SAME
    # (cik, date, source) now carries a different adj_close and a different
    # split_factor_at_ingest. PHASE_2.md §4.3: a single get_bars window must
    # never mix two adjustment bases.
    writer = PostgresPriceWriter(pg_connection)
    repo = PostgresPriceRepository(pg_connection)
    # A ticker that cannot collide with a real company's data already sitting
    # in this shared dev database from an actual backfill run — "NVDA" itself
    # would trigger AmbiguousTickerError against the real NVDA CIK's rows.
    original = _row(ticker="ZZZTEST", d="2024-01-02", adj_close="1000", split_factor="1")
    writer.upsert_bars([original])

    rebased = _row(ticker="ZZZTEST", d="2024-01-02", adj_close="100", split_factor="10")
    result = writer.upsert_bars([rebased])

    assert result.inserted == 0
    assert result.updated == 1
    bars = repo.get_bars("ZZZTEST", date(2024, 1, 1), date(2024, 1, 31), _AS_OF)
    assert len(bars) == 1
    assert bars[0].adj_close == Decimal("100")  # the NEW value, not the stale 1000


def _content_hash(pg_connection: Connection, cik: Cik) -> str:
    """A deterministic hash over every stored column that matters for
    `cik` — everything except `id` (an arbitrary sequence value) and
    `ingested_at` (changes on every write, by design, even when the row's
    actual content doesn't). PHASE_2.md §6's DoD asks for idempotency
    "verified by row count and content hash", not row/insert counts alone
    (`docs/phases/PHASE_1_NOTES.md` established the same standard for
    `fundamental_facts`) — this is that hash, computed straight from the
    table rather than through `PriceBar`, which drops `cik`/`source`/
    `split_factor_at_ingest` and so can't see everything a re-ingestion
    could silently change.
    """
    t = prices
    stmt = (
        sa.select(
            t.c.ticker,
            t.c.date,
            t.c.open,
            t.c.high,
            t.c.low,
            t.c.close,
            t.c.volume,
            t.c.adj_close,
            t.c.adj_high,
            t.c.split_factor_at_ingest,
            t.c.source,
        )
        .where(t.c.cik == cik.value)
        .order_by(t.c.date, t.c.source)
    )
    rows = pg_connection.execute(stmt).all()
    canonical = "|".join(",".join(str(value) for value in row) for row in rows)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_content_hash_is_identical_across_a_re_upsert_of_unchanged_data(
    pg_connection: Connection,
) -> None:
    writer = PostgresPriceWriter(pg_connection)
    rows = [
        _row(ticker="ZZZTEST", d="2024-01-02", adj_close="100"),
        _row(ticker="ZZZTEST", d="2024-01-03", adj_close="101"),
        _row(ticker="ZZZTEST", d="2024-01-04", adj_close="99"),
    ]
    writer.upsert_bars(rows)
    hash_after_first_run = _content_hash(pg_connection, _CIK_A)

    result = writer.upsert_bars(rows)  # identical content, re-ingested

    assert result.inserted == 0
    assert result.updated == 3
    assert _content_hash(pg_connection, _CIK_A) == hash_after_first_run


def test_content_hash_changes_when_a_value_actually_changes(pg_connection: Connection) -> None:
    # Sanity check on the hash function itself: it must not be trivially
    # insensitive to a real content change (e.g. hashing only row count).
    writer = PostgresPriceWriter(pg_connection)
    writer.upsert_bars([_row(ticker="ZZZTEST", d="2024-01-02", adj_close="100")])
    hash_before = _content_hash(pg_connection, _CIK_A)

    writer.upsert_bars([_row(ticker="ZZZTEST", d="2024-01-02", adj_close="200")])

    assert _content_hash(pg_connection, _CIK_A) != hash_before


def test_upsert_bars_is_a_no_op_for_an_empty_sequence(pg_connection: Connection) -> None:
    writer = PostgresPriceWriter(pg_connection)

    result = writer.upsert_bars([])

    assert result.inserted == 0
    assert result.updated == 0


def test_get_bars_raises_on_a_ticker_shared_by_two_ciks(pg_connection: Connection) -> None:
    # A genuine ticker-recycling collision: two different companies' rows
    # both stored under "DUP". PHASE_2.md §0.1's chosen resolution keeps
    # get_bars ticker-keyed on the reasoning that ingestion-time attribution
    # should prevent this — this proves that if it ever doesn't, the read
    # fails loudly instead of silently blending two companies' prices.
    writer = PostgresPriceWriter(pg_connection)
    writer.upsert_bars(
        [
            _row(cik=_CIK_A, ticker="DUP", d="2024-01-02"),
            _row(cik=_CIK_B, ticker="DUP", d="2024-01-03"),
        ]
    )
    repo = PostgresPriceRepository(pg_connection)

    with pytest.raises(AmbiguousTickerError) as exc_info:
        repo.get_bars("DUP", date(2024, 1, 1), date(2024, 1, 31), _AS_OF)

    assert _CIK_A.value in exc_info.value.ciks
    assert _CIK_B.value in exc_info.value.ciks


def test_get_bars_excludes_dates_after_as_of(pg_connection: Connection) -> None:
    writer = PostgresPriceWriter(pg_connection)
    # Not a real ticker, for the same reason as above — avoids colliding with
    # an actual company's data already in this shared dev database.
    writer.upsert_bars(
        [
            _row(ticker="ZZZTEST", d="2015-01-10"),
            _row(ticker="ZZZTEST", d="2015-01-20"),
        ]
    )
    repo = PostgresPriceRepository(pg_connection)

    result = repo.get_bars(
        "ZZZTEST", date(2015, 1, 1), date(2015, 1, 31), AsOfDate.parse("2015-01-15")
    )

    assert [b.date for b in result] == [date(2015, 1, 10)]


def _action(
    *,
    cik: Cik = _CIK_A,
    ticker: str = "TEST",
    source: str = "fake",
    d: str = "2024-06-10",
    event_type: str = "split",
    ratio: str = "10",
) -> CorporateActionRow:
    return CorporateActionRow(
        cik=cik,
        ticker=ticker,
        source=source,
        event_date=date.fromisoformat(d),
        event_type=event_type,
        ratio_or_amount=Decimal(ratio),
    )


def test_upsert_actions_reports_new_rows_as_inserted(pg_connection: Connection) -> None:
    writer = PostgresPriceWriter(pg_connection)

    result = writer.upsert_actions([_action(d="2024-06-10"), _action(d="2024-07-01")])

    assert result.inserted == 2
    assert result.updated == 0


def test_re_upserting_identical_actions_reports_updated_not_inserted(
    pg_connection: Connection,
) -> None:
    writer = PostgresPriceWriter(pg_connection)
    action = _action(d="2024-06-10")
    writer.upsert_actions([action])

    result = writer.upsert_actions([action])

    assert result.inserted == 0
    assert result.updated == 1


def test_upsert_actions_is_a_no_op_for_an_empty_sequence(pg_connection: Connection) -> None:
    writer = PostgresPriceWriter(pg_connection)

    result = writer.upsert_actions([])

    assert result.inserted == 0
    assert result.updated == 0
