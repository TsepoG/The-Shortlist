"""Fixtures for tests that need a real PostgreSQL database.

Skip policy (per the phase 1 plan, §1): skip when `DATABASE_URL` is unset, the
database is unreachable, or the schema hasn't been migrated yet — but only
*locally*. Under CI, all three become hard failures instead, because a silently
skipped integration suite would make PHASE_1.md §7's required check ("Phase 0
guard tests pass unchanged against the PostgreSQL implementation") meaningless:
CI would report green without ever having run it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from shortlist.config import is_ci
from shortlist.data._backends.postgres import (
    PostgresFactRepository,
    PostgresFactWriter,
    PostgresPriceRepository,
    PostgresPriceWriter,
)
from shortlist.data._backends.schema import fundamental_facts, prices
from shortlist.data.factory import wrap_fact_repository, wrap_price_repository
from shortlist.data.repository import FactRepository, PriceReader
from shortlist.data.types import Cik, Fact, PriceBar, PriceRow

# Fixed identity for the price contract's synthetic rows — the ticker itself
# (tests/contract/price_repository_contract.py's TICKER) is already chosen to
# never collide with a real company; this CIK is likewise never a real one.
_CONTRACT_CIK = Cik.parse("0000000001")
_CONTRACT_SOURCE = "contract-test"


def _unavailable(reason: str) -> None:
    if is_ci():
        pytest.fail(f"{reason} — required in CI (PHASE_1.md §7 depends on this suite running)")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        _unavailable("DATABASE_URL is not set")
        return  # pragma: no cover - _unavailable always skips or fails

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(sa.select(1))
    except sa.exc.OperationalError as exc:
        engine.dispose()
        _unavailable(f"DATABASE_URL is unreachable: {exc}")
        return  # pragma: no cover

    try:
        with engine.connect() as conn:
            conn.execute(sa.select(sa.func.count()).select_from(fundamental_facts))
            conn.execute(sa.select(sa.func.count()).select_from(prices))
    except sa.exc.ProgrammingError as exc:
        engine.dispose()
        _unavailable(
            "fundamental_facts/prices is not migrated yet "
            f"(run `uv run alembic upgrade head`): {exc}"
        )
        return  # pragma: no cover

    yield engine
    engine.dispose()


@pytest.fixture
def pg_connection(pg_engine: Engine) -> Iterator[Connection]:
    """A connection inside a transaction that is always rolled back, so tests
    never need to truncate the table between cases and never see each other's
    data even when run in the same session.
    """
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def pg_fact_repo_factory(pg_connection: Connection) -> Callable[..., FactRepository]:
    """Matches `tests.fakes.guarded_fact_repo`'s shape exactly:
    `(*facts: Fact) -> FactRepository`, so every function in
    `tests/contract/fact_repository_contract.py` runs unchanged against this.
    """

    def make(*facts: Fact) -> FactRepository:
        if facts:
            PostgresFactWriter(pg_connection).insert_facts(facts)
        return wrap_fact_repository(PostgresFactRepository(pg_connection))

    return make


def _bar_to_price_row(bar: PriceBar) -> PriceRow:
    return PriceRow(
        cik=_CONTRACT_CIK,
        ticker=bar.ticker,
        source=_CONTRACT_SOURCE,
        date=bar.date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        adj_close=bar.adj_close,
        adj_high=bar.adj_high,
        split_factor_at_ingest=Decimal(1),
    )


@pytest.fixture
def pg_price_repo_factory(pg_connection: Connection) -> Callable[..., PriceReader]:
    """Matches `tests.fakes.guarded_price_repo`'s shape exactly:
    `(*bars: PriceBar) -> PriceReader`, so every function in
    `tests/contract/price_repository_contract.py` runs unchanged against this.

    `PriceBar` (the read type) has no `cik`/`source` — storage needs both, so
    each bar is attributed to a single fixed, never-real CIK/source before
    writing. The contract scenarios never mix tickers within one `make(...)`
    call, so this fixed attribution never triggers `AmbiguousTickerError`.
    """

    def make(*bars: PriceBar) -> PriceReader:
        if bars:
            PostgresPriceWriter(pg_connection).upsert_bars([_bar_to_price_row(b) for b in bars])
        return wrap_price_repository(PostgresPriceRepository(pg_connection))

    return make
