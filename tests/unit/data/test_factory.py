"""PHASE_1.md §5 — the PostgreSQL FactRepository is wired into the factory.

`create_repositories` (which bundles both facts and prices) still raises: prices
are out of scope for phase 1. `create_fact_repository` is the phase 1 seam for
facts alone. Neither constructs a real connection here — these are unit tests
(no DB, no network) — `bind` is supplied so no `DATABASE_URL` needs to exist for
this file to run.
"""

import pytest
import sqlalchemy as sa

from shortlist.data.factory import Backend, create_fact_repository, create_repositories
from shortlist.data.guard import GuardedFactRepository


def test_create_repositories_postgres_still_not_implemented_pending_prices() -> None:
    with pytest.raises(NotImplementedError):
        create_repositories(Backend.POSTGRES)


def test_create_fact_repository_postgres_returns_guarded_instance() -> None:
    # An Engine that is never connected to — constructing it doesn't touch the
    # network, and PostgresFactRepository doesn't connect until a query runs.
    engine = sa.create_engine("postgresql+psycopg://unused:unused@localhost/unused")

    repo = create_fact_repository(Backend.POSTGRES, bind=engine)

    assert isinstance(repo, GuardedFactRepository)
