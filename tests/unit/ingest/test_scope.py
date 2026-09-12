"""The phase 1 ingestion scope file loader — parsing and resolution."""

from pathlib import Path

import pytest

from shortlist.data.types import Cik
from shortlist.ingest.scope import (
    PHASE_1_CIK_OVERRIDES,
    UnresolvedTickerError,
    load_scope,
    parse_scope_tickers,
)
from shortlist.ingest.tickers import TickerDirectory, parse_company_tickers

_SAMPLE_TEXT = """\
# a full-line comment
AAPL   # inline comment

MSFT
aapl
"""

# Recorded subset of the real company_tickers.json (fetched 2026-09-12; 10,426
# records total). These five are the scope-file tickers that genuinely resolve
# through the live file. XLNX/MXIM/CY/MLNX are absent *by design* — that is
# what the real file returned, and it is the condition PHASE_1_CIK_OVERRIDES
# exists to handle.
_RECORDED_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "3": {"cik_str": 97476, "ticker": "TXN", "title": "TEXAS INSTRUMENTS INC"},
    "4": {"cik_str": 2488, "ticker": "AMD", "title": "ADVANCED MICRO DEVICES INC"},
}


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
    scope_file.write_text("NOTATICKER\n")
    directory = TickerDirectory(())  # empty: nothing resolves

    with pytest.raises(UnresolvedTickerError) as excinfo:
        # Explicit overrides={} so this asserts the error path itself rather
        # than depending on what PHASE_1_CIK_OVERRIDES happens to contain.
        load_scope(scope_file, directory, overrides={})

    assert excinfo.value.ticker == "NOTATICKER"


def test_load_scope_honors_manual_override_for_delisted_ticker(tmp_path: Path) -> None:
    scope_file = tmp_path / "scope.txt"
    scope_file.write_text("XLNX\n")
    directory = TickerDirectory(())  # empty: current tickers file has nothing

    # 743988 is the hand-verified Xilinx CIK (see PHASE_1_CIK_OVERRIDES) — an
    # ad hoc override passed explicitly here, independent of the built-in default.
    ciks = load_scope(scope_file, directory, overrides={"XLNX": Cik.parse(743988)})

    assert ciks == (Cik.parse(743988),)


def test_delisted_tickers_resolve_via_builtin_overrides(tmp_path: Path) -> None:
    # No `overrides` argument passed: PHASE_1_CIK_OVERRIDES is the default, and
    # the directory has nothing (matching what the real company_tickers.json
    # returns for these four), so only the built-in overrides can resolve them.
    scope_file = tmp_path / "scope.txt"
    scope_file.write_text("XLNX\nMXIM\nCY\nMLNX\n")
    directory = TickerDirectory(())

    ciks = load_scope(scope_file, directory)

    assert ciks == (
        Cik.parse(743988),
        Cik.parse(743316),
        Cik.parse(791915),
        Cik.parse(1356104),
    )


def test_builtin_overrides_do_not_shadow_a_resolvable_ticker() -> None:
    # Overrides are consulted before the directory (see load_scope), so an
    # override for a ticker that becomes resolvable again would silently win.
    # None of today's four delisted overrides should collide with a currently
    # resolvable ticker.
    assert set(PHASE_1_CIK_OVERRIDES) & set(_RECORDED_TICKERS) == set()


def test_load_scope_on_the_real_phase1_scope_file_parses_without_error() -> None:
    # Not a network test: confirms the checked-in file is well-formed and every
    # line is a plausible ticker token, so a typo is caught immediately rather
    # than at backfill time.
    real_path = Path(__file__).parents[3] / "config" / "ingest_scope_phase1.txt"
    tickers = parse_scope_tickers(real_path.read_text(encoding="utf-8"))

    assert "AAPL" in tickers
    assert "XLNX" in tickers
    assert len(tickers) == len(set(tickers))


def test_load_scope_on_the_real_phase1_scope_file_resolves_all_nine(tmp_path: Path) -> None:
    # This is the assertion that would have caught the original limitation:
    # five tickers resolve via the (recorded) live tickers file, and the four
    # delisted/acquired ones resolve only via PHASE_1_CIK_OVERRIDES.
    real_path = Path(__file__).parents[3] / "config" / "ingest_scope_phase1.txt"
    directory = TickerDirectory(parse_company_tickers(_RECORDED_TICKERS))

    ciks = load_scope(real_path, directory)

    assert ciks == (
        Cik.parse(320193),  # AAPL, from the directory
        Cik.parse(789019),  # MSFT, from the directory
        Cik.parse(1045810),  # NVDA, from the directory
        Cik.parse(97476),  # TXN, from the directory
        Cik.parse(2488),  # AMD, from the directory
        Cik.parse(743988),  # XLNX, from PHASE_1_CIK_OVERRIDES
        Cik.parse(743316),  # MXIM, from PHASE_1_CIK_OVERRIDES
        Cik.parse(791915),  # CY, from PHASE_1_CIK_OVERRIDES
        Cik.parse(1356104),  # MLNX, from PHASE_1_CIK_OVERRIDES
    )
