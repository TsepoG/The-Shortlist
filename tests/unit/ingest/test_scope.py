"""The phase 1 ingestion scope file loader — parsing and resolution."""

from pathlib import Path

import pytest

from shortlist.data.types import Cik
from shortlist.ingest.scope import UnresolvedTickerError, load_scope, parse_scope_tickers
from shortlist.ingest.tickers import TickerDirectory, parse_company_tickers

_SAMPLE_TEXT = """\
# a full-line comment
AAPL   # inline comment

MSFT
aapl
"""


def test_parse_scope_tickers_ignores_comments_and_blank_lines() -> None:
    tickers = parse_scope_tickers(_SAMPLE_TEXT)
    assert tickers == ("AAPL", "MSFT")


def test_parse_scope_tickers_deduplicates_case_insensitively() -> None:
    # "AAPL" and "aapl" in the sample must not both appear.
    tickers = parse_scope_tickers(_SAMPLE_TEXT)
    assert tickers.count("AAPL") == 1


def test_load_scope_resolves_tickers_to_ciks(tmp_path: Path) -> None:
    scope_file = tmp_path / "scope.txt"
    scope_file.write_text("AAPL\nMSFT\n")
    directory = TickerDirectory(
        parse_company_tickers(
            {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
            }
        )
    )

    ciks = load_scope(scope_file, directory)

    assert ciks == (Cik.parse(320193), Cik.parse(789019))


def test_load_scope_raises_a_named_error_for_unresolved_ticker(tmp_path: Path) -> None:
    scope_file = tmp_path / "scope.txt"
    scope_file.write_text("XLNX\n")
    directory = TickerDirectory(())  # empty: nothing resolves

    with pytest.raises(UnresolvedTickerError) as excinfo:
        load_scope(scope_file, directory)

    assert excinfo.value.ticker == "XLNX"


def test_load_scope_honors_manual_override_for_delisted_ticker(tmp_path: Path) -> None:
    scope_file = tmp_path / "scope.txt"
    scope_file.write_text("XLNX\n")
    directory = TickerDirectory(())  # empty: current tickers file has nothing

    ciks = load_scope(scope_file, directory, overrides={"XLNX": Cik.parse(1109354)})

    assert ciks == (Cik.parse(1109354),)


def test_load_scope_on_the_real_phase1_scope_file_parses_without_error() -> None:
    # Not a network test: just confirms the checked-in file is well-formed and
    # every line is a plausible ticker token, so a typo is caught immediately
    # rather than at backfill time.
    real_path = Path(__file__).parents[3] / "config" / "ingest_scope_phase1.txt"
    tickers = parse_scope_tickers(real_path.read_text(encoding="utf-8"))

    assert "AAPL" in tickers
    assert "XLNX" in tickers
    assert len(tickers) == len(set(tickers))
