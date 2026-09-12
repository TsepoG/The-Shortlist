"""Concept normalization: the ordered alias chains from PHASE_1.md §3, and
nothing more.

Companies tag the same economic concept differently — revenue alone may appear
as `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`,
`SalesRevenueNet`, and others. `ALIAS_CHAINS` is the ordered mapping from raw
XBRL tags to canonical concepts: try each alias in order, take the first that
yields a value for the exact period being resolved.

These chains are transcribed **exactly** as PHASE_1.md §3 lists them — no alias
has been added beyond what the spec names. §3 calls them "a starting point, to
be validated empirically against coverage" (§6's unmapped-tag report); CLAUDE.md
is explicit that extending an alias list without evidence is exactly the kind of
invention to stop and ask about, not decide alone. Any addition must be recorded
here as a new `ConceptAlias` with a `motivated_by` note naming the company and
accession that required it — never silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from shortlist.data.types import CanonicalConcept, Unit

# The two XBRL namespaces this phase resolves against. A tag with any other
# namespace (a company's own custom extension) is never silently mapped here —
# PHASE_1.md §3: "Custom extension tags ... are not silently mapped. Log them;
# add explicitly if a fixture company requires it." See companyfacts.py, which
# records unclaimed tags rather than guessing.
US_GAAP = "us-gaap"
DEI = "dei"


@dataclass(frozen=True, slots=True)
class ConceptAlias:
    """One raw XBRL tag that resolves to a canonical concept, plus why it's here."""

    namespace: str
    tag: str
    source: str  # "PHASE_1.md §3" for the starting list; "motivated_by: TICKER, accn" later


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    """A canonical concept's ordered alias chain and expected unit.

    `aliases` is tried **in order** — PHASE_1.md §3: "try each in order, take
    the first that yields a value for the period." Resolution is per-period,
    not per-company: a company may use `Revenues` before the ASC 606 transition
    (~2018) and `RevenueFromContractWithCustomerExcludingAssessedTax` after, and
    both filings must resolve correctly.
    """

    concept: CanonicalConcept
    expected_unit: Unit
    aliases: tuple[ConceptAlias, ...]


def _spec(*tags: str, namespace: str = US_GAAP) -> tuple[ConceptAlias, ...]:
    return tuple(ConceptAlias(namespace, tag, source="PHASE_1.md §3") for tag in tags)


# Ordered exactly as PHASE_1.md §3 lists them. The revenue chain's order matters
# beyond "try each" — §3 notes the ASC 606 transition around 2018: pre-2018
# filings mostly use `Revenues`/`SalesRevenueNet`, post-2018 the
# `RevenueFromContractWithCustomer*` tags. Putting the post-2018 tags first is
# the spec's own choice, transcribed as given, not decided here.
ALIAS_CHAINS: dict[CanonicalConcept, ConceptDefinition] = {
    CanonicalConcept.REVENUE: ConceptDefinition(
        CanonicalConcept.REVENUE,
        Unit.USD,
        _spec(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    ),
    CanonicalConcept.COST_OF_REVENUE: ConceptDefinition(
        CanonicalConcept.COST_OF_REVENUE,
        Unit.USD,
        _spec("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"),
    ),
    CanonicalConcept.GROSS_PROFIT: ConceptDefinition(
        # Derived when absent (see derive.py) — the tagged case is still tried
        # first, per §3: as-filed always wins over a derived figure.
        CanonicalConcept.GROSS_PROFIT,
        Unit.USD,
        _spec("GrossProfit"),
    ),
    CanonicalConcept.OPERATING_INCOME: ConceptDefinition(
        CanonicalConcept.OPERATING_INCOME,
        Unit.USD,
        _spec("OperatingIncomeLoss"),
    ),
    CanonicalConcept.NET_INCOME: ConceptDefinition(
        CanonicalConcept.NET_INCOME,
        Unit.USD,
        _spec(
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
    ),
    CanonicalConcept.TOTAL_ASSETS: ConceptDefinition(
        CanonicalConcept.TOTAL_ASSETS,
        Unit.USD,
        _spec("Assets"),
    ),
    CanonicalConcept.TOTAL_LIABILITIES: ConceptDefinition(
        CanonicalConcept.TOTAL_LIABILITIES,
        Unit.USD,
        _spec("Liabilities"),
    ),
    CanonicalConcept.STOCKHOLDERS_EQUITY: ConceptDefinition(
        CanonicalConcept.STOCKHOLDERS_EQUITY,
        Unit.USD,
        _spec(
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    CanonicalConcept.CURRENT_ASSETS: ConceptDefinition(
        CanonicalConcept.CURRENT_ASSETS,
        Unit.USD,
        _spec("AssetsCurrent"),
    ),
    CanonicalConcept.CURRENT_LIABILITIES: ConceptDefinition(
        CanonicalConcept.CURRENT_LIABILITIES,
        Unit.USD,
        _spec("LiabilitiesCurrent"),
    ),
    CanonicalConcept.CASH: ConceptDefinition(
        CanonicalConcept.CASH,
        Unit.USD,
        _spec(
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    ),
    CanonicalConcept.LONG_TERM_DEBT: ConceptDefinition(
        CanonicalConcept.LONG_TERM_DEBT,
        Unit.USD,
        _spec("LongTermDebtNoncurrent", "LongTermDebt"),
    ),
    CanonicalConcept.OPERATING_CASH_FLOW: ConceptDefinition(
        CanonicalConcept.OPERATING_CASH_FLOW,
        Unit.USD,
        _spec("NetCashProvidedByUsedInOperatingActivities"),
    ),
    CanonicalConcept.CAPEX: ConceptDefinition(
        CanonicalConcept.CAPEX,
        Unit.USD,
        _spec("PaymentsToAcquirePropertyPlantAndEquipment"),
    ),
    CanonicalConcept.SHARES_DILUTED: ConceptDefinition(
        CanonicalConcept.SHARES_DILUTED,
        Unit.SHARES,
        _spec("WeightedAverageNumberOfDilutedSharesOutstanding"),
    ),
    CanonicalConcept.SHARES_OUTSTANDING: ConceptDefinition(
        CanonicalConcept.SHARES_OUTSTANDING,
        Unit.SHARES,
        _spec("EntityCommonStockSharesOutstanding", namespace=DEI),
    ),
}


def expected_unit(concept: CanonicalConcept) -> Unit:
    """The unit a concept's values must be reported in. PHASE_1.md §3: "Never mix
    units ... reject rows whose unit does not match the concept's expected unit."
    """
    return ALIAS_CHAINS[concept].expected_unit


def alias_chain(concept: CanonicalConcept) -> tuple[ConceptAlias, ...]:
    """The ordered aliases for `concept`, first-match-wins."""
    return ALIAS_CHAINS[concept].aliases


def concept_for_tag(namespace: str, tag: str) -> CanonicalConcept | None:
    """Reverse lookup: which canonical concept (if any) claims this raw tag.

    Returns `None` for anything not in `ALIAS_CHAINS` — including every custom
    extension tag — which is exactly what makes an unmapped-tag report possible:
    a tag this function doesn't claim is, by definition, unmapped.
    """
    for definition in ALIAS_CHAINS.values():
        for alias in definition.aliases:
            if alias.namespace == namespace and alias.tag == tag:
                return definition.concept
    return None


def resolve_first_available(
    concept: CanonicalConcept,
    available: dict[tuple[str, str], object],
) -> tuple[ConceptAlias, object] | None:
    """Given the (namespace, tag) -> value pairs available for one exact period,
    return the first alias in `concept`'s chain that has a value, and that value.

    This is the alias-chain half of §3's resolution rule. `available` is scoped
    by the caller to one (period_start, period_end, accession_number) — the
    "for the period" in "take the first that yields a value for the period" —
    so this function itself has no notion of periods at all, only of which tag
    won for whatever slice it was given.
    """
    for alias in alias_chain(concept):
        key = (alias.namespace, alias.tag)
        if key in available:
            return alias, available[key]
    return None
