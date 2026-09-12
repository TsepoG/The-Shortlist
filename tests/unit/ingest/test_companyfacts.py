"""PHASE_1.md §7's required unit tests for the companyfacts parser, run against
recorded/constructed fixture JSON — no network, per CLAUDE.md's unit-test rule.
"""

from decimal import Decimal
from typing import Any

from shortlist.data.types import CanonicalConcept, Cik
from shortlist.ingest.companyfacts import parse_companyfacts

CIK = Cik.parse("0000320193")


def _obs(
    *,
    end: str,
    val: object,
    filed: str | None = "2015-02-15",
    accn: str = "0000320193-15-000001",
    fy: int = 2014,
    fp: str = "FY",
    form: str = "10-K",
    start: str | None = None,
) -> dict[str, Any]:
    obs: dict[str, Any] = {"end": end, "val": val, "fy": fy, "fp": fp, "form": form, "accn": accn}
    if filed is not None:
        obs["filed"] = filed
    if start is not None:
        obs["start"] = start
    return obs


def _payload(namespace: str, tag: str, unit: str, *observations: dict[str, Any]) -> dict[str, Any]:
    return {"facts": {namespace: {tag: {"units": {unit: list(observations)}}}}}


def test_resolves_primary_alias_and_produces_a_fact() -> None:
    payload = _payload(
        "us-gaap",
        "Revenues",
        "USD",
        _obs(end="2014-12-31", val=1000, start="2014-01-01"),
    )

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 1
    f = result.facts[0]
    assert f.concept is CanonicalConcept.REVENUE
    assert f.raw_tag == "Revenues"
    assert f.value == Decimal("1000")
    assert f.filed_date.isoformat() == "2015-02-15"
    assert f.accession_number == "0000320193-15-000001"
    assert f.is_derived is False


def test_resolves_non_primary_alias_still_yields_a_value() -> None:
    # TESTING.md §1.3: "Assert that a company using a non-primary alias still
    # yields a value (regression test for silent nulls)."
    payload = _payload(
        "us-gaap",
        "SalesRevenueGoodsNet",  # last in the revenue alias chain
        "USD",
        _obs(end="2014-12-31", val=42, start="2014-01-01"),
    )

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 1
    assert result.facts[0].concept is CanonicalConcept.REVENUE
    assert result.facts[0].raw_tag == "SalesRevenueGoodsNet"


def test_record_missing_filed_is_dropped_not_defaulted() -> None:
    payload = _payload(
        "us-gaap",
        "Revenues",
        "USD",
        _obs(end="2014-12-31", val=1000, filed=None),
    )

    result = parse_companyfacts(CIK, payload)

    assert result.facts == ()
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "missing_filed"


def test_unit_mismatch_is_rejected() -> None:
    # revenue expects USD; tagged here as shares, which must never happen but
    # must not silently become a USD fact either.
    payload = _payload(
        "us-gaap",
        "Revenues",
        "shares",
        _obs(end="2014-12-31", val=1000),
    )

    result = parse_companyfacts(CIK, payload)

    assert result.facts == ()
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "unit_mismatch"


def test_non_usd_unit_is_logged_and_skipped() -> None:
    # A monetary concept reported in, say, EUR: PHASE_1.md §3 says log and skip,
    # distinct from an outright unit-type mismatch (shares vs USD above).
    payload = {
        "facts": {"us-gaap": {"Revenues": {"units": {"EUR": [_obs(end="2014-12-31", val=900)]}}}}
    }

    result = parse_companyfacts(CIK, payload)

    assert result.facts == ()
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "non_usd_unit"


def test_unrecognized_non_currency_unit_is_a_mismatch_not_logged_as_currency() -> None:
    # Not "USD", not a plausible 3-letter currency code, not shares/pure either
    # — must not be miscategorized as a foreign-currency amount.
    payload = _payload(
        "us-gaap",
        "Revenues",
        "USDollarsPerBarrel",
        _obs(end="2014-12-31", val=1000),
    )

    result = parse_companyfacts(CIK, payload)

    assert result.facts == ()
    assert result.rejections[0].reason == "unit_mismatch"


def test_unmapped_custom_tag_is_recorded_not_silently_dropped() -> None:
    payload = _payload(
        "acme",
        "AcmeSpecialRevenueMetric",
        "USD",
        _obs(end="2014-12-31", val=1000),
    )

    result = parse_companyfacts(CIK, payload)

    assert result.facts == ()
    assert result.rejections == ()
    assert len(result.unmapped_tags) == 1
    assert result.unmapped_tags[0].namespace == "acme"
    assert result.unmapped_tags[0].tag == "AcmeSpecialRevenueMetric"
    assert result.unmapped_tags[0].count == 1


def test_unmapped_tags_are_ranked_by_frequency() -> None:
    payload = {
        "facts": {
            "acme": {
                "TagA": {
                    "units": {
                        "USD": [
                            _obs(end="2014-12-31", val=1),
                            _obs(end="2015-12-31", val=2),
                        ]
                    }
                },
                "TagB": {"units": {"USD": [_obs(end="2014-12-31", val=3)]}},
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    assert [t.tag for t in result.unmapped_tags] == ["TagA", "TagB"]
    assert result.unmapped_tags[0].count == 2


def test_balance_sheet_instant_has_no_period_start() -> None:
    payload = _payload(
        "us-gaap",
        "Assets",
        "USD",
        _obs(end="2014-12-31", val=5000, start=None),
    )

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 1
    assert result.facts[0].period_start is None
    assert result.facts[0].concept is CanonicalConcept.TOTAL_ASSETS


def test_january_fiscal_year_end_label_is_preserved_verbatim() -> None:
    # NVDA-style: FY ends late January, and the fy/fp label as reported can
    # differ from what the calendar year of period_end might suggest. The
    # parser must store fy/fp exactly as given — no reinterpretation.
    payload = _payload(
        "us-gaap",
        "Revenues",
        "USD",
        _obs(end="2015-01-25", val=1000, start="2014-01-27", fy=2015, fp="FY"),
    )

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 1
    f = result.facts[0]
    assert f.period_end.isoformat() == "2015-01-25"
    assert f.fiscal_year == 2015
    assert f.fiscal_period == "FY"


def test_derives_gross_profit_when_absent() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {"USD": [_obs(end="2014-12-31", val=1000, start="2014-01-01")]}
                },
                "CostOfGoodsSold": {
                    "units": {"USD": [_obs(end="2014-12-31", val=400, start="2014-01-01")]}
                },
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    derived = [f for f in result.facts if f.concept is CanonicalConcept.GROSS_PROFIT]
    assert len(derived) == 1
    assert derived[0].is_derived is True
    assert derived[0].value == Decimal("600")


def test_as_filed_gross_profit_is_not_overridden_by_derivation() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {"USD": [_obs(end="2014-12-31", val=1000, start="2014-01-01")]}
                },
                "CostOfGoodsSold": {
                    "units": {"USD": [_obs(end="2014-12-31", val=400, start="2014-01-01")]}
                },
                "GrossProfit": {
                    "units": {"USD": [_obs(end="2014-12-31", val=650, start="2014-01-01")]}
                },
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    gross_profits = [f for f in result.facts if f.concept is CanonicalConcept.GROSS_PROFIT]
    assert len(gross_profits) == 1
    assert gross_profits[0].is_derived is False
    assert gross_profits[0].value == Decimal("650")  # as-filed, not 1000-400=600


def test_no_gross_profit_derived_when_cost_of_revenue_absent() -> None:
    payload = _payload(
        "us-gaap",
        "Revenues",
        "USD",
        _obs(end="2014-12-31", val=1000, start="2014-01-01"),
    )

    result = parse_companyfacts(CIK, payload)

    assert all(f.concept is not CanonicalConcept.GROSS_PROFIT for f in result.facts)


def test_all_parsed_facts_carry_the_given_cik() -> None:
    payload = _payload("us-gaap", "Assets", "USD", _obs(end="2014-12-31", val=1))
    result = parse_companyfacts(CIK, payload)
    assert all(f.cik == CIK for f in result.facts)


def test_empty_payload_produces_nothing() -> None:
    result = parse_companyfacts(CIK, {"facts": {}})
    assert result.facts == ()
    assert result.unmapped_tags == ()
    assert result.rejections == ()
