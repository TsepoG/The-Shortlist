"""PHASE_1.md §3 and §7: the alias chain resolves in order, falls through
correctly, and covers all 16 canonical concepts with the expected units.
"""

from shortlist.data.types import CanonicalConcept, Unit
from shortlist.ingest.concepts import (
    ALIAS_CHAINS,
    DEI,
    US_GAAP,
    alias_chain,
    concept_for_tag,
    expected_unit,
    resolve_first_available,
)


def test_every_canonical_concept_has_an_alias_chain() -> None:
    assert set(ALIAS_CHAINS.keys()) == set(CanonicalConcept)


def test_alias_chain_resolves_in_order() -> None:
    available: dict[tuple[str, str], object] = {
        (US_GAAP, "Revenues"): 100,
        (US_GAAP, "RevenueFromContractWithCustomerExcludingAssessedTax"): 200,
    }

    result = resolve_first_available(CanonicalConcept.REVENUE, available)

    assert result is not None
    alias, value = result
    assert alias.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert value == 200


def test_alias_chain_falls_through_when_first_tag_absent() -> None:
    # Pre-ASC-606 filing: only the older tag is present.
    available: dict[tuple[str, str], object] = {(US_GAAP, "SalesRevenueNet"): 300}

    result = resolve_first_available(CanonicalConcept.REVENUE, available)

    assert result is not None
    alias, value = result
    assert alias.tag == "SalesRevenueNet"
    assert value == 300


def test_alias_chain_falls_through_to_last_resort() -> None:
    available: dict[tuple[str, str], object] = {(US_GAAP, "SalesRevenueGoodsNet"): 42}

    result = resolve_first_available(CanonicalConcept.REVENUE, available)

    assert result is not None
    assert result[0].tag == "SalesRevenueGoodsNet"


def test_resolve_returns_none_when_nothing_available() -> None:
    # A non-primary-alias regression check (TESTING.md §1.3): a company using a
    # custom tag not in the chain at all yields no value here — silent nulls are
    # exactly what §1.3 asks to guard against, so this must be an explicit None,
    # not an exception or a zero.
    result = resolve_first_available(CanonicalConcept.REVENUE, {})
    assert result is None


def test_shares_outstanding_uses_dei_namespace() -> None:
    chain = alias_chain(CanonicalConcept.SHARES_OUTSTANDING)
    assert chain[0].namespace == DEI
    assert chain[0].tag == "EntityCommonStockSharesOutstanding"


def test_monetary_concepts_expect_usd() -> None:
    for concept in CanonicalConcept:
        if concept in (CanonicalConcept.SHARES_DILUTED, CanonicalConcept.SHARES_OUTSTANDING):
            continue
        assert expected_unit(concept) == Unit.USD


def test_share_concepts_expect_shares() -> None:
    assert expected_unit(CanonicalConcept.SHARES_DILUTED) == Unit.SHARES
    assert expected_unit(CanonicalConcept.SHARES_OUTSTANDING) == Unit.SHARES


def test_concept_for_tag_resolves_known_alias() -> None:
    assert concept_for_tag(US_GAAP, "Revenues") == CanonicalConcept.REVENUE
    assert concept_for_tag(US_GAAP, "NetIncomeLoss") == CanonicalConcept.NET_INCOME


def test_concept_for_tag_returns_none_for_unmapped_tag() -> None:
    # A custom extension tag — PHASE_1.md §3: "not silently mapped."
    assert concept_for_tag(US_GAAP, "AcmeCorpSpecialRevenueMetric") is None


def test_concept_for_tag_is_namespace_sensitive() -> None:
    # The same tag string under the wrong namespace does not resolve — a
    # us-gaap tag and a same-named custom-namespace tag are not the same thing.
    assert concept_for_tag("acme", "Revenues") is None
