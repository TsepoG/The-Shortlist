"""Point-in-time data access layer.

The only way anything outside `shortlist.data` may reach stored facts or prices.
This package deliberately exports no concrete repository implementation — only the
protocols, the core types, `LookAheadError`, and the factory functions. A raw,
unguarded repository is not obtainable through this module; see `factory.py`.
"""

from shortlist.data.factory import (
    Backend,
    RepositoryBundle,
    create_fact_repository,
    create_fact_writer,
    create_repositories,
    wrap_fact_repository,
    wrap_price_repository,
)
from shortlist.data.guard import LookAheadError
from shortlist.data.repository import FactRepository, PriceReader, PriceRepository
from shortlist.data.types import (
    AsOfDate,
    CanonicalConcept,
    Cik,
    Fact,
    PriceBar,
    Unit,
)

__all__ = [
    "AsOfDate",
    "Backend",
    "CanonicalConcept",
    "Cik",
    "Fact",
    "FactRepository",
    "LookAheadError",
    "PriceBar",
    "PriceReader",
    "PriceRepository",
    "RepositoryBundle",
    "Unit",
    "create_fact_repository",
    "create_fact_writer",
    "create_repositories",
    "wrap_fact_repository",
    "wrap_price_repository",
]
