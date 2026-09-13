"""The public entry point to storage. This is how "a raw repository instance must
not be obtainable by normal application code" (`PHASE_0.md` §4) is enforced:

- Concrete backend implementations live under `shortlist.data._backends` (private)
  and are never re-exported from `shortlist.data`.
- `create_fact_repository` / `create_repositories` are the only ways application
  code obtains repositories, and both always return them wrapped by the guard.
- `wrap_fact_repository` / `wrap_price_repository` are the dependency-injection seam
  used by tests (and by `create_fact_repository` itself): they accept an
  unguarded implementation and only ever return a guarded one, so there is no
  direction in which an unguarded instance can escape through this module.

Phase 1 wired `Backend.POSTGRES` to a real `FactRepository`; phase 2 adds a real
`PriceReader`/price writer (`_backends/postgres.py`), so `create_repositories`
now returns both. `create_fact_repository` / `create_price_reader` remain
available individually for callers that only need one half.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from shortlist.config import database_url
from shortlist.data.guard import GuardedFactRepository, GuardedPriceRepository
from shortlist.data.repository import FactRepository, PriceReader, PriceRepository

if TYPE_CHECKING:
    # Type-only: shortlist.data must not depend on shortlist.ingest at runtime
    # (the data layer sits below ingestion — DESIGN.md §2's layering), but
    # create_fact_writer's return type still needs to be checkable against
    # what backfill.py's structural Protocol requires.
    from shortlist.ingest.backfill import FactWriter

    # Same reasoning for prices: PostgresPriceWriter is typed structurally
    # against this Protocol, defined in the ingestion layer that owns writing
    # (PHASE_2.md §0.1 / docs/phases/PHASE_2_NOTES.md §0.1).
    from shortlist.ingest.prices.loader import PriceWriter


class Backend(Enum):
    """Storage backends `create_fact_repository` / `create_repositories` know how
    to construct.
    """

    POSTGRES = auto()


@dataclass(frozen=True, slots=True)
class RepositoryBundle:
    """The guarded repositories application code depends on."""

    facts: FactRepository
    prices: PriceReader


def wrap_fact_repository(inner: FactRepository) -> FactRepository:
    """Wrap an unguarded `FactRepository` implementation, returning a guarded one."""
    return GuardedFactRepository(inner)


def wrap_price_repository(inner: PriceRepository) -> PriceReader:
    """Wrap an unguarded `PriceRepository` implementation, returning a guarded
    `PriceReader` — which also exposes `get_trailing_high` (see `repository.py`).
    """
    return GuardedPriceRepository(inner)


def create_price_reader(
    backend: Backend,
    *,
    bind: Engine | Connection | None = None,
) -> PriceReader:
    """Construct just the guarded `PriceReader` for `backend`.

    Mirrors `create_fact_repository` exactly — see that function's docstring
    for the `bind` seam.
    """
    if backend is Backend.POSTGRES:
        from shortlist.data._backends.postgres import PostgresPriceRepository

        engine_or_connection = bind if bind is not None else sa.create_engine(database_url())
        return wrap_price_repository(PostgresPriceRepository(engine_or_connection))
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover


def create_price_writer(
    backend: Backend,
    *,
    bind: Engine | Connection | None = None,
) -> PriceWriter:
    """Construct the price writer for `backend` (ingestion's upsert path).

    Deliberately **not** guarded and **not** part of `PriceRepository` or any
    read protocol — mirrors `create_fact_writer`'s reasoning exactly. Typed
    against `shortlist.ingest.prices.loader.PriceWriter` (a `@runtime_checkable`
    Protocol, imported only under `TYPE_CHECKING`) for the same layering reason.
    """
    if backend is Backend.POSTGRES:
        from shortlist.data._backends.postgres import PostgresPriceWriter

        engine_or_connection = bind if bind is not None else sa.create_engine(database_url())
        return PostgresPriceWriter(engine_or_connection)
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover


def create_fact_repository(
    backend: Backend,
    *,
    bind: Engine | Connection | None = None,
) -> FactRepository:
    """Construct just the guarded `FactRepository` for `backend`.

    `bind` is the dependency-injection seam for tests: pass a transaction-scoped
    `Connection` to run against a rolled-back transaction instead of a fresh
    connection pool. Application code omits it and gets an `Engine` built from
    `DATABASE_URL`.
    """
    if backend is Backend.POSTGRES:
        from shortlist.data._backends.postgres import PostgresFactRepository

        engine_or_connection = bind if bind is not None else sa.create_engine(database_url())
        return wrap_fact_repository(PostgresFactRepository(engine_or_connection))
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover


def create_fact_writer(
    backend: Backend,
    *,
    bind: Engine | Connection | None = None,
) -> FactWriter:
    """Construct the fact writer for `backend` (ingestion's append path).

    Deliberately **not** guarded and **not** part of `FactRepository` or any
    read protocol — the guard constrains what reads may return and has
    nothing to say about an honest write of a fact's true `filed_date`
    (see `docs/phases/PHASE_1_NOTES.md` §9). Typed against
    `shortlist.ingest.backfill.FactWriter` (a `@runtime_checkable` Protocol,
    imported only under `TYPE_CHECKING`) so callers get real type-checking
    without this module depending on `shortlist.ingest` at runtime, and so
    `shortlist.data` still exports no concrete repository/writer
    implementation by name — callers never import
    `shortlist.data._backends.postgres` directly.
    """
    if backend is Backend.POSTGRES:
        from shortlist.data._backends.postgres import PostgresFactWriter

        engine_or_connection = bind if bind is not None else sa.create_engine(database_url())
        return PostgresFactWriter(engine_or_connection)
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover


def create_repositories(
    backend: Backend,
    *,
    bind: Engine | Connection | None = None,
) -> RepositoryBundle:
    """Construct the guarded repository bundle (facts + prices) for `backend`.

    `bind` is the same dependency-injection seam as the individual `create_*`
    functions, passed through to both.
    """
    if backend is Backend.POSTGRES:
        return RepositoryBundle(
            facts=create_fact_repository(backend, bind=bind),
            prices=create_price_reader(backend, bind=bind),
        )
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover
