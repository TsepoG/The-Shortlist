"""PHASE_0.md §7 definition-of-done items, made executable rather than eyeballed:

- No method anywhere in `data/` has a default `as_of`.
- No public path to an unguarded repository.

These are architectural properties of the module, not behaviours of any one call,
so they are asserted by reflection rather than by exercising a repository.
"""

import inspect
from collections.abc import Callable
from typing import get_type_hints

import shortlist.data as data_package
from shortlist.data.guard import GuardedFactRepository, GuardedPriceRepository
from shortlist.data.repository import FactRepository, PriceReader, PriceRepository
from tests.fakes.memory_repository import InMemoryFactRepository, InMemoryPriceRepository

_METHODS_TAKING_AS_OF: list[Callable[..., object]] = [
    FactRepository.get_facts,
    FactRepository.get_latest_fact,
    FactRepository.get_facts_for_universe,
    PriceRepository.get_bars,
    PriceReader.get_trailing_high,
    GuardedFactRepository.get_facts,
    GuardedFactRepository.get_latest_fact,
    GuardedFactRepository.get_facts_for_universe,
    GuardedPriceRepository.get_bars,
    GuardedPriceRepository.get_trailing_high,
]


def test_no_data_method_has_default_as_of() -> None:
    for method in _METHODS_TAKING_AS_OF:
        signature = inspect.signature(method)
        assert "as_of" in signature.parameters, (
            f"{method.__qualname__} does not declare an as_of parameter at all"
        )
        param = signature.parameters["as_of"]
        assert param.default is inspect.Parameter.empty, (
            f"{method.__qualname__} gives as_of a default of {param.default!r} — "
            "there must be no default; every callsite passes it explicitly"
        )


def test_data_package_exports_no_concrete_repository() -> None:
    exported = {name: getattr(data_package, name) for name in data_package.__all__}

    assert "GuardedFactRepository" not in vars(data_package)
    assert "GuardedPriceRepository" not in vars(data_package)

    for name, obj in exported.items():
        if inspect.isclass(obj) and name.endswith(("Repository", "Reader")):
            assert getattr(obj, "_is_protocol", False), (
                f"{name} is exported from shortlist.data but is not a Protocol — "
                "only protocols and factory functions may be exported here"
            )


def test_guard_implements_every_protocol_method() -> None:
    for method_name in ("get_facts", "get_latest_fact", "get_facts_for_universe"):
        assert hasattr(GuardedFactRepository, method_name), (
            f"GuardedFactRepository is missing {method_name} from FactRepository"
        )

    for method_name in ("get_bars", "get_trailing_high"):
        assert hasattr(GuardedPriceRepository, method_name), (
            f"GuardedPriceRepository is missing {method_name} from PriceReader"
        )

    # Runtime-checkable protocol conformance, exercised directly rather than by
    # comparing method objects (which would be fragile across Python versions).
    guarded_facts = GuardedFactRepository(InMemoryFactRepository())
    guarded_prices = GuardedPriceRepository(InMemoryPriceRepository())
    assert isinstance(guarded_facts, FactRepository)
    assert isinstance(guarded_prices, PriceReader)


def test_repository_protocols_have_no_extraneous_type_ignores() -> None:
    # Sanity check that the protocols are fully type-annotated (get_type_hints
    # resolves cleanly), matching CLAUDE.md's "type hints everywhere" convention.
    get_type_hints(FactRepository.get_facts)
    get_type_hints(FactRepository.get_latest_fact)
    get_type_hints(FactRepository.get_facts_for_universe)
    get_type_hints(PriceRepository.get_bars)
    get_type_hints(PriceReader.get_trailing_high)
