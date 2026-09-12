"""The bulk-archive backfill: PHASE_1.md §1's "Use the bulk archive for the
initial backfill, not per-company API calls."

Streams each in-scope company's `companyfacts` JSON out of a downloaded
`companyfacts.zip`, parses it (`companyfacts.py`), and writes the result
through `PostgresFactWriter`. Restartable by construction: every insert is
`ON CONFLICT DO NOTHING` (`docs/phases/PHASE_1_NOTES.md` §0.1's unique
constraint), so an interrupted run is simply re-run — see PHASE_1.md §5's
"Re-running ingestion must be safe."
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from shortlist.data._backends.postgres import InsertResult
from shortlist.data.types import Cik, Fact
from shortlist.ingest.companyfacts import ParseResult, UnmappedTag, parse_companyfacts
from shortlist.ingest.edgar_client import iter_bulk_companyfacts


@runtime_checkable
class FactWriter(Protocol):
    """What `run_backfill` needs from a writer — matched structurally by
    `PostgresFactWriter`, and by a fake in unit tests (no DB, no network).
    """

    def insert_facts(self, facts: Sequence[Fact]) -> InsertResult: ...


# The zip member filename pattern the bulk archive uses: "CIK##########.json".
_MEMBER_PREFIX = "CIK"
_MEMBER_SUFFIX = ".json"


def cik_from_member_name(name: str) -> Cik | None:
    """`"CIK0000320193.json"` -> `Cik("0000320193")`; `None` for anything else
    (README files and similar are present in the real archive).
    """
    if not (name.startswith(_MEMBER_PREFIX) and name.endswith(_MEMBER_SUFFIX)):
        return None
    digits = name[len(_MEMBER_PREFIX) : -len(_MEMBER_SUFFIX)]
    if not digits.isdigit():
        return None
    try:
        return Cik.parse(digits)
    except ValueError:
        return None


@dataclass
class BackfillSummary:
    """What a backfill run did, for the quality-job artifacts (PHASE_1.md §6)
    and for confirming idempotency empirically (§8's "safe to re-run").
    """

    companies_in_scope: int
    companies_processed: int = 0
    facts_inserted: int = 0
    facts_skipped_as_duplicate: int = 0
    unmapped_tag_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    rejection_counts: dict[str, int] = field(default_factory=dict)

    def record_parse(self, result: ParseResult, insert_result: InsertResult) -> None:
        self.companies_processed += 1
        self.facts_inserted += insert_result.inserted
        self.facts_skipped_as_duplicate += insert_result.skipped
        for unmapped in result.unmapped_tags:
            key = (unmapped.namespace, unmapped.tag)
            self.unmapped_tag_counts[key] = self.unmapped_tag_counts.get(key, 0) + unmapped.count
        for rejection in result.rejections:
            self.rejection_counts[rejection.reason] = (
                self.rejection_counts.get(rejection.reason, 0) + 1
            )

    def merged_unmapped_tags(self) -> tuple[UnmappedTag, ...]:
        return tuple(
            UnmappedTag(namespace, tag, count)
            for (namespace, tag), count in sorted(
                self.unmapped_tag_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )


def run_backfill(
    archive_path: Path,
    scope: Sequence[Cik],
    writer: FactWriter,
) -> BackfillSummary:
    """Ingest every in-`scope` company found in the bulk archive at
    `archive_path`.

    Companies in `scope` but absent from the archive are silently absent from
    the summary's processed count — a real gap, but not this function's job to
    diagnose; the discovery/coverage tooling (PHASE_1.md §6) is where a missing
    company would be noticed.
    """
    scope_set = set(scope)
    summary = BackfillSummary(companies_in_scope=len(scope_set))

    for _cik, result in _iter_in_scope_parsed_facts(archive_path, scope_set):
        insert_result = writer.insert_facts(result.facts)
        summary.record_parse(result, insert_result)

    return summary


def _iter_in_scope_parsed_facts(
    archive_path: Path, scope: set[Cik]
) -> Iterable[tuple[Cik, ParseResult]]:
    def _in_scope(name: str) -> bool:
        cik = cik_from_member_name(name)
        return cik is not None and cik in scope

    for name, payload in iter_bulk_companyfacts(archive_path, include=_in_scope):
        # cik_from_member_name(name) cannot be None here: _in_scope already
        # required it to parse successfully before json.load ever ran.
        cik = cik_from_member_name(name)
        assert cik is not None
        yield cik, parse_companyfacts(cik, payload)
