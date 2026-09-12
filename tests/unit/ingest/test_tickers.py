"""PHASE_1.md §4: resolve tickers to CIKs from company_tickers.json, and treat
ticker -> CIK as not injective over time (tickers get reused after delisting).
"""

from shortlist.data.types import Cik
from shortlist.ingest.tickers import TickerDirectory, parse_company_tickers

_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def test_parse_company_tickers_zero_pads_cik() -> None:
    records = parse_company_tickers(_PAYLOAD)

    ciks = {r.ticker: r.cik for r in records}
    assert ciks["AAPL"] == Cik.parse(320193)
    assert ciks["AAPL"].value == "0000320193"


def test_directory_resolves_ticker_to_cik() -> None:
    directory = TickerDirectory(parse_company_tickers(_PAYLOAD))

    assert directory.cik_for_ticker("AAPL") == Cik.parse(320193)
    assert directory.cik_for_ticker("aapl") == Cik.parse(320193)  # case-insensitive


def test_directory_resolves_cik_to_ticker() -> None:
    directory = TickerDirectory(parse_company_tickers(_PAYLOAD))

    assert directory.ticker_for_cik(Cik.parse(789019)) == "MSFT"


def test_directory_returns_none_for_unknown_ticker() -> None:
    directory = TickerDirectory(parse_company_tickers(_PAYLOAD))

    assert directory.cik_for_ticker("NOTREAL") is None


def test_directory_returns_none_for_unknown_cik() -> None:
    directory = TickerDirectory(parse_company_tickers(_PAYLOAD))

    assert directory.ticker_for_cik(Cik.parse(999999)) is None


def test_directory_len_matches_record_count() -> None:
    directory = TickerDirectory(parse_company_tickers(_PAYLOAD))

    assert len(directory) == 2


def test_a_delisted_companys_old_ticker_can_be_reused_by_a_different_cik() -> None:
    # This snapshot's job is only to map ticker -> CIK *as of this snapshot* —
    # it must not be relied on as a stable historical join. A later snapshot
    # reassigning "XYZ" to a different CIK is expected, not a bug here.
    early = TickerDirectory(
        parse_company_tickers({"0": {"cik_str": 111111, "ticker": "XYZ", "title": "Old Co"}})
    )
    later = TickerDirectory(
        parse_company_tickers({"0": {"cik_str": 222222, "ticker": "XYZ", "title": "New Co"}})
    )

    assert early.cik_for_ticker("XYZ") == Cik.parse(111111)
    assert later.cik_for_ticker("XYZ") == Cik.parse(222222)
