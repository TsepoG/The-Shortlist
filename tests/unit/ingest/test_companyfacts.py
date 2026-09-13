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


def test_two_aliases_for_one_period_and_accession_resolve_by_priority_not_json_order() -> None:
    # Regression test for a real bug found running the phase 1 backfill:
    # PHASE_1.md §3 says alias resolution should "try each in order, take the
    # first that yields a value" — but the parser used to emit a Fact for
    # every matching alias unconditionally, letting the database's insert
    # order (not alias priority) decide which one survived. "Revenues" is
    # higher-priority than "SalesRevenueNet" in the revenue chain; both are
    # given for the exact same period and accession here, with the
    # lower-priority tag listed FIRST in the payload so a JSON-order-dependent
    # bug would pick the wrong one.
    payload: dict[str, Any] = {
        "facts": {
            "us-gaap": {
                "SalesRevenueNet": {
                    "units": {"USD": [_obs(end="2014-12-31", val=999, start="2014-01-01")]}
                },
                "Revenues": {
                    "units": {"USD": [_obs(end="2014-12-31", val=1000, start="2014-01-01")]}
                },
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 1
    assert result.facts[0].raw_tag == "Revenues"
    assert result.facts[0].value == Decimal("1000")

    shadowed = [r for r in result.rejections if r.reason == "shadowed_by_higher_priority_alias"]
    assert len(shadowed) == 1
    assert shadowed[0].tag == "SalesRevenueNet"


def test_two_aliases_for_different_periods_both_yield_facts() -> None:
    # The exclusivity above is scoped to one exact (period, accession) — a
    # company using "Revenues" in one filing and "SalesRevenueNet" in another
    # (the normal case this alias chain exists for) must still get both.
    payload: dict[str, Any] = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _obs(
                                end="2013-12-31",
                                val=900,
                                start="2013-01-01",
                                filed="2014-02-15",
                                accn="0000320193-14-000001",
                            )
                        ]
                    }
                },
                "SalesRevenueNet": {
                    "units": {
                        "USD": [
                            _obs(
                                end="2014-12-31",
                                val=1000,
                                start="2014-01-01",
                                filed="2015-02-15",
                                accn="0000320193-15-000001",
                            )
                        ]
                    }
                },
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    assert len(result.facts) == 2
    raw_tags = {f.raw_tag for f in result.facts}
    assert raw_tags == {"Revenues", "SalesRevenueNet"}
    assert not any(r.reason == "shadowed_by_higher_priority_alias" for r in result.rejections)


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


def _balance_sheet_payload(
    *,
    equity_val: object = 300,
    lse_val: object = 1000,
    liabilities_val: object | None = None,
    other_equity: dict[str, object] | None = None,
    end: str = "2014-12-31",
    accn: str = "0000320193-15-000001",
) -> dict[str, Any]:
    tags: dict[str, Any] = {
        "StockholdersEquity": {
            "units": {"USD": [_obs(end=end, val=equity_val, start=None, accn=accn)]}
        },
        "LiabilitiesAndStockholdersEquity": {
            "units": {"USD": [_obs(end=end, val=lse_val, start=None, accn=accn)]}
        },
    }
    if liabilities_val is not None:
        tags["Liabilities"] = {
            "units": {"USD": [_obs(end=end, val=liabilities_val, start=None, accn=accn)]}
        }
    for tag, val in (other_equity or {}).items():
        tags[tag] = {"units": {"USD": [_obs(end=end, val=val, start=None, accn=accn)]}}
    return {"facts": {"us-gaap": tags}}


def test_derives_total_liabilities_when_absent() -> None:
    payload = _balance_sheet_payload(equity_val=300, lse_val=1000)

    result = parse_companyfacts(CIK, payload)

    derived = [f for f in result.facts if f.concept is CanonicalConcept.TOTAL_LIABILITIES]
    assert len(derived) == 1
    assert derived[0].is_derived is True
    assert derived[0].value == Decimal("700")


def test_as_filed_liabilities_is_not_overridden_by_derivation() -> None:
    payload = _balance_sheet_payload(equity_val=300, lse_val=1000, liabilities_val=650)

    result = parse_companyfacts(CIK, payload)

    liabilities = [f for f in result.facts if f.concept is CanonicalConcept.TOTAL_LIABILITIES]
    assert len(liabilities) == 1
    assert liabilities[0].is_derived is False
    assert liabilities[0].value == Decimal("650")  # as-filed, not 1000-300=700


def test_no_total_liabilities_derived_when_stockholders_equity_absent() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "LiabilitiesAndStockholdersEquity": {
                    "units": {"USD": [_obs(end="2014-12-31", val=1000, start=None)]}
                },
            }
        }
    }

    result = parse_companyfacts(CIK, payload)

    assert all(f.concept is not CanonicalConcept.TOTAL_LIABILITIES for f in result.facts)


def test_no_total_liabilities_derived_when_minority_interest_present() -> None:
    # Regression test for a real risk found while planning this derivation:
    # AMD's alias chain resolves parent-only StockholdersEquity, so naively
    # subtracting it from LiabilitiesAndStockholdersEquity would silently fold
    # a nonzero MinorityInterest into "liabilities" — confirmed against real
    # AMD data to be off by 12.8% at one period. Must refuse to derive instead.
    payload = _balance_sheet_payload(
        equity_val=300, lse_val=1000, other_equity={"MinorityInterest": 50}
    )

    result = parse_companyfacts(CIK, payload)

    assert all(f.concept is not CanonicalConcept.TOTAL_LIABILITIES for f in result.facts)


def test_no_total_liabilities_derived_when_temporary_equity_present() -> None:
    payload = _balance_sheet_payload(
        equity_val=300,
        lse_val=1000,
        other_equity={"TemporaryEquityCarryingAmountAttributableToParent": 20},
    )

    result = parse_companyfacts(CIK, payload)

    assert all(f.concept is not CanonicalConcept.TOTAL_LIABILITIES for f in result.facts)


def test_total_liabilities_derived_when_other_equity_component_is_zero() -> None:
    payload = _balance_sheet_payload(
        equity_val=300, lse_val=1000, other_equity={"MinorityInterest": 0}
    )

    result = parse_companyfacts(CIK, payload)

    derived = [f for f in result.facts if f.concept is CanonicalConcept.TOTAL_LIABILITIES]
    assert len(derived) == 1
    assert derived[0].value == Decimal("700")


def test_liabilities_and_stockholders_equity_is_never_emitted_as_a_fact() -> None:
    # LiabilitiesAndStockholdersEquity is a derivation input, not an alias for
    # any canonical concept (it equals Assets, not total_liabilities) — it
    # must never itself become a stored Fact.
    payload = _balance_sheet_payload(equity_val=300, lse_val=1000)

    result = parse_companyfacts(CIK, payload)

    assert all(f.raw_tag != "LiabilitiesAndStockholdersEquity" for f in result.facts)


def test_liabilities_and_stockholders_equity_is_still_counted_as_unmapped() -> None:
    # It is genuinely unclaimed by any alias chain — the unmapped-tags report
    # must still see it (annotated separately as a derivation input, not a
    # missing alias — see qa/unmapped_tags.py), so its count is not silently
    # dropped just because this module also consumes its value.
    payload = _balance_sheet_payload(equity_val=300, lse_val=1000)

    result = parse_companyfacts(CIK, payload)

    lse_unmapped = [t for t in result.unmapped_tags if t.tag == "LiabilitiesAndStockholdersEquity"]
    assert len(lse_unmapped) == 1
    assert lse_unmapped[0].count == 1


def test_minority_interest_is_still_counted_as_unmapped() -> None:
    payload = _balance_sheet_payload(
        equity_val=300, lse_val=1000, other_equity={"MinorityInterest": 50}
    )

    result = parse_companyfacts(CIK, payload)

    mi_unmapped = [t for t in result.unmapped_tags if t.tag == "MinorityInterest"]
    assert len(mi_unmapped) == 1


def test_all_parsed_facts_carry_the_given_cik() -> None:
    payload = _payload("us-gaap", "Assets", "USD", _obs(end="2014-12-31", val=1))
    result = parse_companyfacts(CIK, payload)
    assert all(f.cik == CIK for f in result.facts)


def test_empty_payload_produces_nothing() -> None:
    result = parse_companyfacts(CIK, {"facts": {}})
    assert result.facts == ()
    assert result.unmapped_tags == ()
    assert result.rejections == ()
