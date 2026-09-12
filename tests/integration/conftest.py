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

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from shortlist.config import is_ci
from shortlist.data._backends.postgres import PostgresFactRepository, PostgresFactWriter
from shortlist.data._backends.schema import fundamental_facts
from shortlist.data.factory import wrap_fact_repository
from shortlist.data.repository import FactRepository
from shortlist.data.types import Fact


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
    except sa.exc.ProgrammingError as exc:
        engine.dispose()
        _unavailable(
            f"fundamental_facts is not migrated yet (run `uv run alembic upgrade head`): {exc}"
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
