"""The public entry point to storage. This is how "a raw repository instance must
not be obtainable by normal application code" (`PHASE_0.md` §4) is enforced:

- Concrete backend implementations live under `shortlist.data._backends` (private)
  and are never re-exported from `shortlist.data`.
- `create_repositories` is the only way application code obtains repositories, and
  it always returns them wrapped by the guard.
- `wrap_fact_repository` / `wrap_price_repository` are the dependency-injection seam
  used by tests (and by later phases wiring in a real backend): they accept an
  unguarded implementation and only ever return a guarded one, so there is no
  direction in which an unguarded instance can escape through this module.

Storage is stubbed in phase 0 — `Backend.POSTGRES` raises `NotImplementedError`, but
the seam this factory defines is the one phase 1 wires a real implementation into.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from shortlist.data.guard import GuardedFactRepository, GuardedPriceRepository
from shortlist.data.repository import FactRepository, PriceReader, PriceRepository


class Backend(Enum):
    """Storage backends `create_repositories` knows how to construct."""

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


def create_repositories(backend: Backend) -> RepositoryBundle:
    """Construct the guarded repository bundle for `backend`.

    Phase 0 stubs storage entirely: no backend is implemented yet, so every value
    of `Backend` raises. Phase 1 wires the PostgreSQL implementation in here,
    behind `wrap_fact_repository` / `wrap_price_repository`, so callers never
    change and never see an unguarded instance.
    """
    if backend is Backend.POSTGRES:
        raise NotImplementedError(
            "PostgreSQL backend arrives in phase 1. "
            "Phase 0 provides only the interface, the guard, and the in-memory fake."
        )
    raise AssertionError(f"Unhandled backend: {backend!r}")  # pragma: no cover
