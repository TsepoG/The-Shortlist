"""Type-level invariants for the core point-in-time types (PHASE_0.md §2)."""

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from shortlist.data.types import AsOfDate, CanonicalConcept, Cik, Fact, Unit


def test_cik_rejects_unpadded_identifier() -> None:
    with pytest.raises(ValueError):
        Cik("320193")


def test_cik_rejects_non_digit_identifier() -> None:
    with pytest.raises(ValueError):
        Cik("00A0320193")


def test_cik_parse_zero_pads() -> None:
    assert Cik.parse(320193) == Cik("0000320193")
    assert Cik.parse("320193") == Cik("0000320193")


def test_cik_parse_rejects_non_digit_value() -> None:
    with pytest.raises(ValueError):
        Cik.parse("not-a-number")


def test_cik_parse_rejects_value_too_long() -> None:
    with pytest.raises(ValueError):
        Cik.parse("123456789012")


def test_asof_date_rejects_bare_datetime_argument() -> None:
    # A datetime.datetime is a datetime.date subclass — mypy accepts it wherever a
    # date is expected — but it is not a plain date and carries a time component
    # that has no meaning for an as-of cutoff, so the runtime check must catch it.
    with pytest.raises(TypeError):
        AsOfDate(dt.datetime(2015, 3, 1, 12, 0, 0))


def test_asof_date_rejects_string_argument() -> None:
    with pytest.raises(TypeError):
        AsOfDate("2015-03-01")  # type: ignore[arg-type]


def test_asof_date_parse_accepts_iso_string() -> None:
    assert AsOfDate.parse("2015-03-01") == AsOfDate(dt.date(2015, 3, 1))


def test_asof_date_has_no_today_constructor() -> None:
    # AsOfDate must never be constructible as "now" — there is deliberately no
    # such classmethod.
    assert not hasattr(AsOfDate, "today")


def test_fact_is_immutable() -> None:
    instance = Fact(
        cik=Cik.parse("320193"),
        concept=CanonicalConcept.REVENUE,
        raw_tag="Revenues",
        unit=Unit.USD,
        value=Decimal("1000"),
        period_start=None,
        period_end=dt.date(2014, 12, 31),
        fiscal_year=2014,
        fiscal_period="Q4",
        form="10-K",
        filed_date=dt.date(2015, 2, 15),
        accession_number="0000320193-15-000001",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.value = Decimal("2000")  # type: ignore[misc]
