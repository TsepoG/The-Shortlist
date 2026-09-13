"""Phase 2 CLI: composes the already-tested library functions
(`resolution.ticker_windows`, `loader.run_price_backfill`, `qa.detect_jumps`,
`qa.check_known_events`) into runnable subcommands. No new ingestion logic
lives here — mirrors `run_phase1.py`'s role exactly, including archiving
output as report artifacts rather than printing only (`TESTING.md` §7).

    uv run python -m shortlist.ingest.scripts.run_phase2 backfill
    uv run python -m shortlist.ingest.scripts.run_phase2 qa       --as-of YYYY-MM-DD
    uv run python -m shortlist.ingest.scripts.run_phase2 evidence --as-of YYYY-MM-DD

Scope is always the same nine companies as phase 1
(`shortlist.ingest.scripts.scope_common.resolve_scope`) — PHASE_2.md §3:
"reuse phase 1's scope ... do not build a second scope mechanism."

`evidence` is not one of PHASE_2.md's named subcommands. It originally
gathered the real-data evidence `docs/phases/PHASE_2_NOTES.md` §0.2 needed
before the trailing-high basis decision was made; that decision is now made
(intraday basis — see §4), so this subcommand is kept as a monitoring tool,
since the decision is explicitly scoped to today's five large, liquid-cap
companies and should be re-checked once thinner/illiquid names enter scope.
It reads real stored `adj_high` values and writes nothing to `prices`.
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from shortlist.data import (
    AsOfDate,
    Backend,
    CanonicalConcept,
    Cik,
    FactRepository,
    PriceReader,
    create_fact_repository,
    create_price_reader,
    create_price_writer,
)
from shortlist.data.types import PriceBar
from shortlist.ingest.prices.loader import PriceBackfillSummary, run_price_backfill
from shortlist.ingest.prices.provider import YahooPriceProvider
from shortlist.ingest.prices.qa import (
    KNOWN_EVENTS,
    KnownEventViolation,
    PriceJump,
    check_known_events,
    detect_jumps,
)
from shortlist.ingest.prices.resolution import TickerNotResolvedError, ticker_for_cik
from shortlist.ingest.qa.report import write_report
from shortlist.ingest.scripts.scope_common import cached_ticker_directory, resolve_scope
from shortlist.ingest.tickers import TickerDirectory

# One year, for the §0.2 evidence-gathering window — not a spec value, chosen
# because a 52-week trailing high is the window DESIGN.md §4.4 names for the
# dip screen itself. A plain timedelta, not date.replace(year=...), so it
# never trips on a Feb 29 boundary.
_EVIDENCE_TRAILING_WINDOW = timedelta(days=365)
_EVIDENCE_STEP_DAYS = 21  # roughly monthly, to keep the report readable
_MATERIAL_DIFFERENCE_THRESHOLD = Decimal("0.01")  # 1%
_BARS_LOOKBACK = timedelta(days=365 * 40)  # enough to cover this universe's full history


# --- backfill -----------------------------------------------------------------


def _render_backfill_markdown(summary: PriceBackfillSummary, scope: Sequence[Cik]) -> str:
    lines = [
        "# Phase 2 price backfill",
        "",
        f"Scope: {len(scope)} companies "
        f"({summary.companies_processed}/{summary.companies_in_scope} attempted).",
        f"Bars: {summary.bars_inserted} inserted, {summary.bars_updated} updated.",
        f"Actions: {summary.actions_inserted} inserted, {summary.actions_updated} updated.",
        "",
    ]
    if summary.companies_with_no_data:
        lines.append(
            f"**{len(summary.companies_with_no_data)} companies with no price data at "
            "all from this provider** (expected for delisted/acquired companies the "
            "provider doesn't carry — see docs/phases/PHASE_2_NOTES.md):"
        )
        lines.append("")
        for ticker in summary.companies_with_no_data:
            lines.append(f"- {ticker}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _run_backfill() -> int:
    scope = resolve_scope()
    directory = cached_ticker_directory()
    fact_repo = create_fact_repository(Backend.POSTGRES)
    writer = create_price_writer(Backend.POSTGRES)
    as_of = AsOfDate(date.today())

    with YahooPriceProvider() as provider:
        summary = run_price_backfill(list(scope), directory, fact_repo, provider, writer, as_of)

    report_dir = write_report(
        "price_backfill",
        summary_markdown=_render_backfill_markdown(summary, scope),
        data={
            "scope": [c.value for c in scope],
            "companies_in_scope": summary.companies_in_scope,
            "companies_processed": summary.companies_processed,
            "bars_inserted": summary.bars_inserted,
            "bars_updated": summary.bars_updated,
            "actions_inserted": summary.actions_inserted,
            "actions_updated": summary.actions_updated,
            "companies_with_no_data": summary.companies_with_no_data,
        },
    )
    print(f"Price backfill report written to {report_dir}")
    print(
        f"companies_processed={summary.companies_processed} "
        f"bars_inserted={summary.bars_inserted} bars_updated={summary.bars_updated} "
        f"companies_with_no_data={summary.companies_with_no_data}"
    )
    return 0


# --- shared: fetch every scope company's bars, keyed by ticker --------------


def _bars_by_ticker(
    scope: Sequence[Cik],
    directory: TickerDirectory,
    price_repo: PriceReader,
    as_of: AsOfDate,
) -> dict[str, tuple[PriceBar, ...]]:
    """Every in-scope CIK's bars, keyed by the ticker it resolves to.

    A CIK phase 1 has no ticker for at all (neither the live directory nor
    `PHASE_1_CIK_OVERRIDES` names one) is skipped — that would be a phase 1
    scope-file problem, not a phase 2 one, and the CIKs this codebase ingests
    always resolve one way or the other.
    """
    result: dict[str, tuple[PriceBar, ...]] = {}
    for cik in scope:
        try:
            ticker = ticker_for_cik(cik, directory)
        except TickerNotResolvedError:
            continue
        bars = price_repo.get_bars(ticker, as_of.value - _BARS_LOOKBACK, as_of.value, as_of)
        if bars:
            result[ticker] = tuple(bars)
    return result


# --- qa -------------------------------------------------------------------


def _render_qa_markdown(
    jumps_by_ticker: dict[str, tuple[PriceJump, ...]],
    known_violations: tuple[KnownEventViolation, ...],
) -> str:
    lines = ["# Phase 2 price QA", ""]
    total_jumps = sum(len(j) for j in jumps_by_ticker.values())
    lines.append(f"{total_jumps} single-day jump(s) beyond threshold across the universe.")
    lines.append(f"{len(known_violations)} known-event violation(s) (expect 0).")
    lines.append("")
    if total_jumps:
        lines.append("| ticker | date | pct_change | matched_known_event | near_filing_date |")
        lines.append("|---|---|---|---|---|")
        for ticker, jumps in jumps_by_ticker.items():
            for j in jumps:
                lines.append(
                    f"| {ticker} | {j.date} | {j.pct_change:.2%} | "
                    f"{j.matched_known_event} | {j.near_filing_date} |"
                )
        lines.append("")
    if known_violations:
        lines.append("## Known-event violations")
        lines.append("")
        for v in known_violations:
            lines.append(f"- {v.event.ticker} {v.event.event_date}: {v.pct_change:.2%}")
    return "\n".join(lines) + "\n"


def _run_qa(as_of: AsOfDate) -> int:
    scope = resolve_scope()
    directory = cached_ticker_directory()
    fact_repo: FactRepository = create_fact_repository(Backend.POSTGRES)
    price_repo = create_price_reader(Backend.POSTGRES)

    known_event_dates = frozenset(e.event_date for e in KNOWN_EVENTS)
    concepts = list(CanonicalConcept)

    bars_by_ticker: dict[str, tuple[PriceBar, ...]] = {}
    jumps_by_ticker: dict[str, tuple[PriceJump, ...]] = {}
    for cik in scope:
        try:
            ticker = ticker_for_cik(cik, directory)
        except TickerNotResolvedError:
            continue
        bars = price_repo.get_bars(ticker, as_of.value - _BARS_LOOKBACK, as_of.value, as_of)
        if not bars:
            continue
        bars_by_ticker[ticker] = tuple(bars)
        # Own filing dates only — pooling the whole universe's filed_dates
        # would let an unrelated company's filing coincidentally "explain" a
        # jump that has nothing to do with it.
        own_filing_dates = frozenset(
            f.filed_date for f in fact_repo.get_facts_for_universe([cik], concepts, as_of)
        )
        jumps_by_ticker[ticker] = detect_jumps(
            bars, known_event_dates=known_event_dates, filing_dates=own_filing_dates
        )

    known_violations = check_known_events(bars_by_ticker)

    report_dir = write_report(
        "price_qa",
        summary_markdown=_render_qa_markdown(jumps_by_ticker, known_violations),
        data={
            "as_of": as_of.value,
            "jumps": {t: list(j) for t, j in jumps_by_ticker.items() if j},
            "known_event_violations": list(known_violations),
        },
    )
    total_jumps = sum(len(j) for j in jumps_by_ticker.values())
    print(f"Price QA report written to {report_dir}")
    print(f"jumps={total_jumps} known_event_violations={len(known_violations)}")
    return 0


# --- evidence (docs/phases/PHASE_2_NOTES.md §0.2 and §4) ---------------------
#
# §0.2's decision is made: intraday basis (max(adj_high)), recorded in
# PHASE_2_NOTES.md §4 and implemented in guard.py/loader.py. This subcommand
# now compares against the REAL stored adj_high (loader.py's
# _approximate_adj_high, no longer recomputed here) rather than gathering
# pre-decision evidence — kept as a monitoring tool, since §4's decision is
# explicitly scoped to the current five large, liquid-cap companies and
# should be re-run once thinner/illiquid names enter scope.


@dataclass(frozen=True, slots=True)
class _EvidenceWindow:
    window_end: date
    max_adj_close: Decimal
    max_adj_high: Decimal
    pct_difference: Decimal
    max_high_date_is_a_jump: bool


def _evidence_for_ticker(bars: Sequence[PriceBar]) -> list[_EvidenceWindow]:
    sorted_bars = sorted(bars, key=lambda b: b.date)
    jump_dates = {j.date for j in detect_jumps(sorted_bars)}
    windows: list[_EvidenceWindow] = []
    for i in range(0, len(sorted_bars), _EVIDENCE_STEP_DAYS):
        window_end_bar = sorted_bars[i]
        window_start = window_end_bar.date - _EVIDENCE_TRAILING_WINDOW
        window = [b for b in sorted_bars if window_start <= b.date <= window_end_bar.date]
        if not window:
            continue
        max_adj_close = max(b.adj_close for b in window)
        if max_adj_close == 0:
            continue
        max_high_date, max_adj_high = max(
            ((b.date, b.adj_high) for b in window), key=lambda t: t[1]
        )
        pct_diff = (max_adj_high - max_adj_close) / max_adj_close
        windows.append(
            _EvidenceWindow(
                window_end=window_end_bar.date,
                max_adj_close=max_adj_close,
                max_adj_high=max_adj_high,
                pct_difference=pct_diff,
                max_high_date_is_a_jump=max_high_date in jump_dates,
            )
        )
    return windows


def _render_evidence_markdown(windows_by_ticker: dict[str, list[_EvidenceWindow]]) -> str:
    lines = [
        "# Phase 2 §0.2/§4 monitoring: closing-basis vs intraday-basis trailing high",
        "",
        "Decision already made (docs/phases/PHASE_2_NOTES.md §4): intraday basis "
        "(max(adj_high)) is what get_trailing_high actually computes now. This report "
        "compares against the real stored adj_high, as an ongoing check that the "
        "decision still holds as scope grows beyond today's five large, liquid caps. "
        "A window's 'difference' is (max adj_high - max adj_close) / max adj_close, "
        "over a trailing 365-day window sampled roughly monthly.",
        "",
        "| ticker | windows | windows with >1% difference | fraction | of those, "
        "max-high date is a flagged jump |",
        "|---|---|---|---|---|",
    ]
    all_diffs: list[Decimal] = []
    for ticker, windows in windows_by_ticker.items():
        if not windows:
            continue
        material = [w for w in windows if abs(w.pct_difference) > _MATERIAL_DIFFERENCE_THRESHOLD]
        jump_linked = [w for w in material if w.max_high_date_is_a_jump]
        all_diffs.extend(w.pct_difference for w in windows)
        fraction = len(material) / len(windows)
        lines.append(
            f"| {ticker} | {len(windows)} | {len(material)} | {fraction:.1%} | "
            f"{len(jump_linked)}/{len(material)} |"
        )
    if all_diffs:
        lines.append("")
        lines.append(
            f"Overall median difference: {statistics.median(all_diffs):.2%}; "
            f"max observed: {max(all_diffs, key=abs):.2%}."
        )
    return "\n".join(lines) + "\n"


def _run_evidence(as_of: AsOfDate) -> int:
    scope = resolve_scope()
    directory = cached_ticker_directory()
    price_repo = create_price_reader(Backend.POSTGRES)
    bars_by_ticker = _bars_by_ticker(scope, directory, price_repo, as_of)

    windows_by_ticker = {
        ticker: _evidence_for_ticker(bars) for ticker, bars in bars_by_ticker.items()
    }

    report_dir = write_report(
        "price_evidence_0_2",
        summary_markdown=_render_evidence_markdown(windows_by_ticker),
        data={"as_of": as_of.value},
    )
    print(f"§0.2 evidence report written to {report_dir}")
    return 0


# --- CLI ---------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shortlist.ingest.scripts.run_phase2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("backfill")

    qa_parser = subparsers.add_parser("qa")
    qa_parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")

    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")

    args = parser.parse_args(argv)

    if args.command == "backfill":
        return _run_backfill()
    if args.command == "qa":
        return _run_qa(AsOfDate.parse(args.as_of))
    if args.command == "evidence":
        return _run_evidence(AsOfDate.parse(args.as_of))
    raise AssertionError(f"Unhandled command: {args.command!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
