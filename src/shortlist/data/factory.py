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

Phase 1 wires `Backend.POSTGRES` to a real `FactRepository`
(`_backends/postgres.py`). Prices remain unimplemented — `create_repositories`
(which returns both facts and prices) still raises until phase 2 supplies a price
backend; `create_fact_repository` is the phase 1 seam for facts alone, so callers
that only need facts are not blocked on prices existing.
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


def create_repositories(backend: Backend) -> RepositoryBundle:
    """Construct the guarded repository bundle for `backend`.

    Raises until phase 2 supplies a price backend — prices are out of scope for
    phase 1 (`PHASE_1.md`: "Do not build in this phase: prices..."). Callers that
    need only facts should use `create_fact_repository` instead of waiting on
    this to stop raising.
    """
    if backend is Backend.POSTGRES:
        raise NotImplementedError(
            "The price backend arrives in phase 2. "
            "Phase 1 provides create_fact_repository() for facts alone; "
            "create_repositories() needs both facts and prices."
        )
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover
