"""In-memory fakes for the point-in-time data access layer.

`guarded_fact_repo` / `guarded_price_repo` build the correct in-memory fake and
wrap it through the real `shortlist.data.factory` seam, so every phase 0 test
exercises the actual enforcement path rather than a bare fake. Tests that need the
deliberately broken fakes (for `PHASE_0.md` §6.4) import `LeakingFactRepository` /
`LeakingPriceRepository` directly from `tests.fakes.leaking_repository`.
"""

from collections.abc import Sequence

from shortlist.data.factory import wrap_fact_repository, wrap_price_repository
from shortlist.data.repository import FactRepository, PriceReader
from shortlist.data.types import Fact, PriceBar
from tests.fakes.memory_repository import InMemoryFactRepository, InMemoryPriceRepository


def guarded_fact_repo(*facts: Fact) -> FactRepository:
    """A guarded, correctly-implemented `FactRepository` over `facts`."""
    return wrap_fact_repository(InMemoryFactRepository(facts))


def guarded_price_repo(*bars: PriceBar) -> PriceReader:
    """A guarded, correctly-implemented `PriceReader` over `bars`."""
    return wrap_price_repository(InMemoryPriceRepository(bars))


__all__: Sequence[str] = ["guarded_fact_repo", "guarded_price_repo"]
