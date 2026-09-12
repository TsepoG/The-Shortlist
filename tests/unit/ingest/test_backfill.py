"""The bulk-archive backfill orchestrator — no DB, no network.

Uses a fake `FactWriter` (matching `backfill.FactWriter` structurally, same as
`PostgresFactWriter` does) so these tests never touch Postgres, and a small
synthetic zip archive standing in for a real `companyfacts.zip`.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

from shortlist.data._backends.postgres import InsertResult
from shortlist.data.types import Cik, Fact
from shortlist.ingest.backfill import cik_from_member_name, run_backfill


class FakeFactWriter:
    """Records every batch it's asked to insert; always reports 0 skipped —
    idempotency itself is PostgresFactWriter's job (tested in
    tests/integration/), not backfill's.
    """

    def __init__(self) -> None:
        self.batches: list[Sequence[Fact]] = []

    def insert_facts(self, facts: Sequence[Fact]) -> InsertResult:
        self.batches.append(facts)
        return InsertResult(inserted=len(facts), skipped=0)


def _apple_companyfacts() -> dict[str, object]:
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "end": "2014-12-31",
                                "start": "2014-01-01",
                                "val": 1000,
                                "accn": "0000320193-15-000001",
                                "fy": 2014,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2015-02-15",
                            }
                        ]
                    }
                }
            }
        }
    }


def _write_archive(path: Path, members: dict[str, dict[str, object]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, json.dumps(payload))


def test_cik_from_member_name_parses_standard_filename() -> None:
    assert cik_from_member_name("CIK0000320193.json") == Cik.parse("0000320193")


def test_cik_from_member_name_rejects_non_matching_files() -> None:
    assert cik_from_member_name("README.txt") is None
    assert cik_from_member_name("CIKnotanumber.json") is None
    assert cik_from_member_name("SOMETHING_ELSE.json") is None


def test_backfill_ingests_only_in_scope_companies(tmp_path: Path) -> None:
    archive = tmp_path / "companyfacts.zip"
    _write_archive(
        archive,
        {
            "CIK0000320193.json": _apple_companyfacts(),  # in scope
            "CIK0000789019.json": _apple_companyfacts(),  # not in scope
            "README.txt": {},  # not a company at all
        },
    )
    writer = FakeFactWriter()

    summary = run_backfill(archive, [Cik.parse("0000320193")], writer)

    assert summary.companies_processed == 1
    assert len(writer.batches) == 1
    assert writer.batches[0][0].cik == Cik.parse("0000320193")


def test_backfill_summary_counts_inserted_facts(tmp_path: Path) -> None:
    archive = tmp_path / "companyfacts.zip"
    _write_archive(archive, {"CIK0000320193.json": _apple_companyfacts()})
    writer = FakeFactWriter()

    summary = run_backfill(archive, [Cik.parse("0000320193")], writer)

    assert summary.facts_inserted == 1
    assert summary.facts_skipped_as_duplicate == 0


def test_backfill_accumulates_unmapped_tags_across_companies(tmp_path: Path) -> None:
    custom_tag_payload: dict[str, object] = {
        "facts": {"acme": {"WeirdMetric": {"units": {"USD": [{"end": "2014-12-31", "val": 1}]}}}}
    }
    archive = tmp_path / "companyfacts.zip"
    _write_archive(
        archive,
        {
            "CIK0000000001.json": custom_tag_payload,
            "CIK0000000002.json": custom_tag_payload,
        },
    )
    writer = FakeFactWriter()

    summary = run_backfill(archive, [Cik.parse("0000000001"), Cik.parse("0000000002")], writer)

    assert summary.merged_unmapped_tags()[0].tag == "WeirdMetric"
    assert summary.merged_unmapped_tags()[0].count == 2


def test_backfill_accumulates_rejection_counts(tmp_path: Path) -> None:
    missing_filed_payload: dict[str, object] = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"end": "2014-12-31", "val": 1}]}}}}
    }
    archive = tmp_path / "companyfacts.zip"
    _write_archive(archive, {"CIK0000000001.json": missing_filed_payload})
    writer = FakeFactWriter()

    summary = run_backfill(archive, [Cik.parse("0000000001")], writer)

    assert summary.rejection_counts.get("missing_filed") == 1


def test_backfill_company_absent_from_archive_is_simply_not_processed(tmp_path: Path) -> None:
    archive = tmp_path / "companyfacts.zip"
    _write_archive(archive, {})  # empty archive
    writer = FakeFactWriter()

    summary = run_backfill(archive, [Cik.parse("0000320193")], writer)

    assert summary.companies_in_scope == 1
    assert summary.companies_processed == 0
    assert writer.batches == []
