"""PHASE_1.md §5 / PHASE_2.md §0.1 — the PostgreSQL Fact/Price repositories are
wired into the factory.

`create_repositories` bundles both facts and prices, real as of phase 2. None
of these tests construct a real connection — these are unit tests (no DB, no
network) — `bind` is supplied so no `DATABASE_URL` needs to exist for this
file to run.
"""

import sqlalchemy as sa

from shortlist.data._backends.postgres import PostgresFactWriter, PostgresPriceWriter
from shortlist.data.factory import (
    Backend,
    create_fact_repository,
    create_fact_writer,
    create_price_reader,
    create_price_writer,
    create_repositories,
)
from shortlist.data.guard import GuardedFactRepository, GuardedPriceRepository
from shortlist.ingest.backfill import FactWriter
from shortlist.ingest.prices.loader import PriceWriter


def test_create_repositories_postgres_returns_a_real_bundle() -> None:
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    bundle = create_repositories(Backend.POSTGRES, bind=engine)

    assert isinstance(bundle.facts, GuardedFactRepository)
    assert isinstance(bundle.prices, GuardedPriceRepository)


def test_create_fact_repository_postgres_returns_guarded_instance() -> None:
    # An Engine that is never connected to — constructing it doesn't touch the
    # network, and PostgresFactRepository doesn't connect until a query runs.
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    repo = create_fact_repository(Backend.POSTGRES, bind=engine)

    assert isinstance(repo, GuardedFactRepository)


def test_create_fact_writer_postgres_returns_a_writer() -> None:
    # Same never-connected-Engine idiom as above — no DB, no network.
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    writer = create_fact_writer(Backend.POSTGRES, bind=engine)

    assert isinstance(writer, PostgresFactWriter)
    # Structural conformance to backfill.py's FactWriter Protocol — this is
    # the contract callers actually depend on, not the concrete class.
    assert isinstance(writer, FactWriter)


def test_create_price_reader_postgres_returns_guarded_instance() -> None:
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    repo = create_price_reader(Backend.POSTGRES, bind=engine)

    assert isinstance(repo, GuardedPriceRepository)


def test_create_price_writer_postgres_returns_a_writer() -> None:
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    writer = create_price_writer(Backend.POSTGRES, bind=engine)

    assert isinstance(writer, PostgresPriceWriter)
    # Structural conformance to loader.py's PriceWriter Protocol — this is the
    # contract callers actually depend on, not the concrete class.
    assert isinstance(writer, PriceWriter)
