"""The thin CLI wrapper `qa/__init__.py`'s docstring describes but never
shipped: composes the already-tested library functions (`scope.load_scope`,
`backfill.run_backfill`, the QA jobs, `discover_fixtures`) into three
runnable subcommands. No new ingestion logic lives here — this module only
wires existing pieces together and renders their output as report artifacts
(`TESTING.md` §7: archived, not printed-only).

    uv run python -m shortlist.ingest.scripts.run_phase1 backfill [--refresh-archive]
    uv run python -m shortlist.ingest.scripts.run_phase1 qa       --as-of YYYY-MM-DD
    uv run python -m shortlist.ingest.scripts.run_phase1 discover --as-of YYYY-MM-DD

Scope is always `config/ingest_scope_phase1.txt` resolved through
`shortlist.ingest.scope.load_scope` with its default overrides
(`PHASE_1_CIK_OVERRIDES`) — never a re-typed or hardcoded CIK list.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

from shortlist.data import (
    AsOfDate,
    Backend,
    CanonicalConcept,
    Cik,
    create_fact_repository,
    create_fact_writer,
)
from shortlist.ingest.backfill import BackfillSummary, run_backfill
from shortlist.ingest.companyfacts import UnmappedTag
from shortlist.ingest.edgar_client import EdgarClient
from shortlist.ingest.qa.coverage import (
    XBRL_PHASE_IN_NOTE,
    CoverageCell,
    cells_below_threshold,
    compute_coverage,
)
from shortlist.ingest.qa.reconciliation import ReconciliationViolation, reconcile
from shortlist.ingest.qa.report import DEFAULT_REPORTS_ROOT, write_report
from shortlist.ingest.qa.unmapped_tags import write_unmapped_tags_report
from shortlist.ingest.scope import load_scope
from shortlist.ingest.scripts.discover_fixtures import (
    CustomTagCandidate,
    FiscalYearChangeCandidate,
    RestatementCandidate,
    SmallerReportingCompanyCandidate,
    find_custom_tag_candidates,
    find_fiscal_year_change_candidates,
    find_restatement_candidates,
    find_smaller_reporting_company_candidates,
)
from shortlist.ingest.tickers import TickerDirectory, parse_company_tickers

DATA_DIR = Path("data")
ARCHIVE_PATH = DATA_DIR / "companyfacts.zip"
ARCHIVE_PART_PATH = DATA_DIR / "companyfacts.zip.part"
TICKERS_CACHE_PATH = DATA_DIR / "company_tickers.json"
SCOPE_PATH = Path("config/ingest_scope_phase1.txt")

_MIN_PLAUSIBLE_ARCHIVE_MEMBERS = 1000  # real archive has ~20k; guards against a tiny/corrupt file


# --- Shared: scope resolution, archive caching -------------------------------


def _cached_ticker_directory() -> TickerDirectory:
    """`company_tickers.json`, fetched once and cached to disk. Every later
    run (qa, discover, or a re-run of backfill) reuses the cache and makes no
    network call for it at all.
    """
    if TICKERS_CACHE_PATH.exists():
        payload = json.loads(TICKERS_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        with EdgarClient() as client:
            payload = client.get_company_tickers()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TICKERS_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return TickerDirectory(parse_company_tickers(payload))


def _resolve_scope() -> tuple[Cik, ...]:
    """The nine phase 1 companies, resolved via `load_scope`'s default
    overrides — never a re-typed CIK list.
    """
    directory = _cached_ticker_directory()
    return load_scope(SCOPE_PATH, directory)


def _archive_is_valid(path: Path) -> bool:
    """Opening a zip reads its central directory, which sits at the END of
    the file — an interrupted download truncates the file before that point,
    so this raises `BadZipFile` reliably without needing to decompress and
    CRC-check all ~18 GiB of members (which `testzip()` would do).
    """
    try:
        with zipfile.ZipFile(path) as archive:
            return len(archive.namelist()) > _MIN_PLAUSIBLE_ARCHIVE_MEMBERS
    except (zipfile.BadZipFile, OSError):
        return False


def _ensure_archive(*, refresh: bool) -> Path:
    """Return a valid local `companyfacts.zip`, downloading only if needed.

    `EdgarClient.download_bulk_companyfacts_archive` always re-downloads and
    truncates its destination unconditionally — caching and validation are
    this function's job, not the client's. Downloads to a `.part` path and
    renames on success, so an interrupted download can never leave a
    truncated file at `ARCHIVE_PATH` that a later run mistakes for valid.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ARCHIVE_PATH.exists() and not refresh:
        if _archive_is_valid(ARCHIVE_PATH):
            size = ARCHIVE_PATH.stat().st_size
            print(f"Using cached archive at {ARCHIVE_PATH} ({size:,} bytes) — no download.")
            return ARCHIVE_PATH
        print(f"Cached archive at {ARCHIVE_PATH} failed validation; re-downloading.")

    print("Downloading companyfacts.zip from SEC EDGAR (~1.3 GiB; several minutes)...")
    with EdgarClient() as client:
        client.download_bulk_companyfacts_archive(ARCHIVE_PART_PATH)
    if not _archive_is_valid(ARCHIVE_PART_PATH):
        raise RuntimeError(f"Downloaded archive at {ARCHIVE_PART_PATH} failed validation.")
    ARCHIVE_PART_PATH.replace(ARCHIVE_PATH)
    size = ARCHIVE_PATH.stat().st_size
    print(f"Downloaded and validated {ARCHIVE_PATH} ({size:,} bytes).")
    return ARCHIVE_PATH


# --- backfill -----------------------------------------------------------------


def _render_backfill_markdown(summary: BackfillSummary, scope: Sequence[Cik]) -> str:
    lines = [
        "# Phase 1 backfill",
        "",
        f"Scope: {len(scope)} companies "
        f"({summary.companies_processed}/{summary.companies_in_scope} found in the archive).",
        f"Facts inserted: {summary.facts_inserted}",
        f"Facts skipped as duplicate: {summary.facts_skipped_as_duplicate}",
        "",
    ]
    if summary.companies_processed != summary.companies_in_scope:
        lines.append(
            "**WARNING: not every in-scope company was found in the archive.** "
            "See `PHASE_1.md` §9's stop condition."
        )
        lines.append("")
    if summary.rejection_counts:
        lines.append("## Rejections")
        lines.append("")
        lines.append("| reason | count |")
        lines.append("|---|---|")
        for reason, count in sorted(summary.rejection_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    unmapped = summary.merged_unmapped_tags()
    lines.append(
        f"{len(unmapped)} unmapped (namespace, tag) pairs — see the separate "
        "unmapped_tags report for the full, frequency-ranked list."
    )
    return "\n".join(lines) + "\n"


def _run_backfill(*, refresh_archive: bool) -> int:
    scope = _resolve_scope()
    archive_path = _ensure_archive(refresh=refresh_archive)

    writer = create_fact_writer(Backend.POSTGRES)
    summary = run_backfill(archive_path, scope, writer)

    unmapped = summary.merged_unmapped_tags()
    write_unmapped_tags_report(unmapped)

    report_data: dict[str, object] = {
        "scope": [c.value for c in scope],
        "companies_in_scope": summary.companies_in_scope,
        "companies_processed": summary.companies_processed,
        "facts_inserted": summary.facts_inserted,
        "facts_skipped_as_duplicate": summary.facts_skipped_as_duplicate,
        "rejection_counts": dict(summary.rejection_counts),
        "unmapped_tags": list(unmapped),
    }
    directory = write_report(
        "backfill",
        summary_markdown=_render_backfill_markdown(summary, scope),
        data=report_data,
    )

    print(f"Backfill report written to {directory}")
    print(
        f"companies_in_scope={summary.companies_in_scope} "
        f"companies_processed={summary.companies_processed} "
        f"facts_inserted={summary.facts_inserted} "
        f"facts_skipped_as_duplicate={summary.facts_skipped_as_duplicate}"
    )
    if summary.companies_processed != summary.companies_in_scope:
        print("WARNING: a scope CIK was not found in the archive. See PHASE_1.md §9.")
        return 1
    return 0


# --- qa -------------------------------------------------------------------


def _render_coverage_markdown(cells: Sequence[CoverageCell], below: Sequence[CoverageCell]) -> str:
    lines = ["# Coverage", "", XBRL_PHASE_IN_NOTE, ""]
    lines.append(f"{len(cells)} (concept, fiscal_year) cells; {len(below)} below threshold.")
    lines.append("")
    lines.append("| concept | fiscal_year | covered/universe | fraction | below threshold |")
    lines.append("|---|---|---|---|---|")
    for c in sorted(cells, key=lambda c: (c.concept, c.fiscal_year)):
        lines.append(
            f"| {c.concept} | {c.fiscal_year} | {c.covered}/{c.universe_size} "
            f"| {c.fraction:.2%} | {'YES' if c.below_threshold else ''} |"
        )
    return "\n".join(lines) + "\n"


def _render_reconciliation_markdown(violations: Sequence[ReconciliationViolation]) -> str:
    lines = ["# Reconciliation", ""]
    if not violations:
        lines.append("No violations.")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(violations)} violation(s).")
    lines.append("")
    lines.append("| cik | period_end | check | expected | actual | difference |")
    lines.append("|---|---|---|---|---|---|")
    for v in violations:
        lines.append(
            f"| {v.cik} | {v.period_end} | {v.check} | {v.expected} | {v.actual} | {v.difference} |"
        )
    return "\n".join(lines) + "\n"


def _run_qa(as_of: AsOfDate) -> int:
    scope = _resolve_scope()
    repo = create_fact_repository(Backend.POSTGRES)
    concepts = list(CanonicalConcept)
    facts_all = repo.get_facts_for_universe(scope, concepts, as_of)

    coverage_cells = compute_coverage(scope, concepts, facts_all)
    below_threshold = cells_below_threshold(coverage_cells)
    coverage_dir = write_report(
        "coverage",
        summary_markdown=_render_coverage_markdown(coverage_cells, below_threshold),
        data={
            "as_of": as_of.value,
            "universe_size": len(set(scope)),
            "cells": list(coverage_cells),
            "below_threshold": list(below_threshold),
            "note": XBRL_PHASE_IN_NOTE,
        },
    )

    # reconcile() does its own same-accession bundle selection per check —
    # it must see every visible fact (restatements included), not a
    # pre-collapsed one-per-concept view. See reconciliation.py.
    violations = reconcile(facts_all)
    reconciliation_dir = write_report(
        "reconciliation",
        summary_markdown=_render_reconciliation_markdown(violations),
        data={"as_of": as_of.value, "violations": list(violations)},
    )

    print(f"Coverage report written to {coverage_dir} ({len(below_threshold)} below threshold)")
    print(f"Reconciliation report written to {reconciliation_dir} ({len(violations)} violation(s))")
    return 0


# --- discover ---------------------------------------------------------------


def _load_unmapped_tags_from_latest_backfill_report() -> tuple[UnmappedTag, ...]:
    """Unmapped tags exist only in a backfill run's own report artifact — there
    is no `unmapped_tags` table, so a later `discover` invocation (a fresh
    process) can only see them by reading the most recent backfill report back.
    """
    backfill_root = DEFAULT_REPORTS_ROOT / "backfill"
    if not backfill_root.exists():
        return ()
    runs = sorted(p for p in backfill_root.iterdir() if p.is_dir())
    if not runs:
        return ()
    payload = json.loads((runs[-1] / "report.json").read_text(encoding="utf-8"))
    tags = payload.get("unmapped_tags", [])
    return tuple(UnmappedTag(t["namespace"], t["tag"], t["count"]) for t in tags)


def _render_discover_markdown(
    restatements: Sequence[RestatementCandidate],
    fiscal_year_changes: Sequence[FiscalYearChangeCandidate],
    smaller_reporting: Sequence[SmallerReportingCompanyCandidate],
    custom_tags: Sequence[CustomTagCandidate],
) -> str:
    lines = [
        "# Fixture candidates (PHASE_1.md §4's four 'identify empirically' cases)",
        "",
        "Ranked candidates only — no company is chosen here. Hand-verify against",
        "the actual filing before treating any of these as a golden fixture.",
        "",
        f"## Restatement candidates ({len(restatements)})",
        "",
    ]
    for r in restatements:
        lines.append(
            f"- {r.cik} {r.concept}: {r.earlier_value} ({r.earlier_accession}) -> "
            f"{r.later_value} ({r.later_accession}), diff {r.difference} — {r.filing_url}"
        )
    lines.append("")
    lines.append(f"## Fiscal year change candidates ({len(fiscal_year_changes)})")
    lines.append("")
    for c in fiscal_year_changes:
        lines.append(
            f"- {c.cik}: FY{c.earlier_fiscal_year} end month "
            f"{c.earlier_period_end.month} -> FY{c.later_fiscal_year} end month "
            f"{c.later_period_end.month} — {c.filing_url}"
        )
    lines.append("")
    lines.append(f"## Smaller reporting company candidates ({len(smaller_reporting)})")
    lines.append("")
    for s in smaller_reporting:
        lines.append(
            f"- {s.cik}: first filed {s.first_filed_date}, "
            f"coverage {s.concept_coverage_fraction:.0%}"
        )
    lines.append("")
    lines.append(f"## Custom tag candidates ({len(custom_tags)})")
    lines.append("")
    for t in custom_tags:
        lines.append(f"- {t.namespace}:{t.tag} (x{t.count}) -> hint: {t.plausible_concept_hint}")
    return "\n".join(lines) + "\n"


def _run_discover(as_of: AsOfDate) -> int:
    scope = _resolve_scope()
    repo = create_fact_repository(Backend.POSTGRES)
    concepts = list(CanonicalConcept)
    facts_all = repo.get_facts_for_universe(scope, concepts, as_of)
    unmapped = _load_unmapped_tags_from_latest_backfill_report()

    restatements = find_restatement_candidates(facts_all)
    fiscal_year_changes = find_fiscal_year_change_candidates(facts_all)
    smaller_reporting = find_smaller_reporting_company_candidates(facts_all, concepts)
    custom_tags = find_custom_tag_candidates(unmapped)

    directory = write_report(
        "fixture_candidates",
        summary_markdown=_render_discover_markdown(
            restatements, fiscal_year_changes, smaller_reporting, custom_tags
        ),
        data={
            "as_of": as_of.value,
            "restatement_candidates": list(restatements),
            "fiscal_year_change_candidates": list(fiscal_year_changes),
            "smaller_reporting_company_candidates": list(smaller_reporting),
            "custom_tag_candidates": list(custom_tags),
        },
    )
    print(f"Fixture-candidate report written to {directory}")
    print(
        f"restatements={len(restatements)} fiscal_year_changes={len(fiscal_year_changes)} "
        f"smaller_reporting={len(smaller_reporting)} custom_tags={len(custom_tags)}"
    )
    return 0


# --- CLI ---------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shortlist.ingest.scripts.run_phase1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser("backfill")
    backfill_parser.add_argument("--refresh-archive", action="store_true")

    qa_parser = subparsers.add_parser("qa")
    qa_parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")

    args = parser.parse_args(argv)

    if args.command == "backfill":
        return _run_backfill(refresh_archive=args.refresh_archive)
    if args.command == "qa":
        return _run_qa(AsOfDate.parse(args.as_of))
    if args.command == "discover":
        return _run_discover(AsOfDate.parse(args.as_of))
    raise AssertionError(f"Unhandled command: {args.command!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
