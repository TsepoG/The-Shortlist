"""TESTING.md §7: reports are archived artifacts, not console output."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shortlist.ingest.qa.report import write_report


@dataclass(frozen=True, slots=True)
class _Row:
    name: str
    value: int


def test_write_report_creates_json_and_markdown(tmp_path: Path) -> None:
    directory = write_report(
        "my_job",
        summary_markdown="# Summary\n",
        data=[{"a": 1}],
        reports_root=tmp_path,
    )

    assert (directory / "report.json").exists()
    assert (directory / "report.md").read_text(encoding="utf-8") == "# Summary\n"
    assert json.loads((directory / "report.json").read_text(encoding="utf-8")) == [{"a": 1}]


def test_write_report_serializes_dataclasses(tmp_path: Path) -> None:
    directory = write_report(
        "my_job",
        summary_markdown="",
        data=[_Row("x", 1)],
        reports_root=tmp_path,
    )

    parsed = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert parsed == [{"name": "x", "value": 1}]


def test_write_report_serializes_dates(tmp_path: Path) -> None:
    from datetime import date

    directory = write_report(
        "my_job",
        summary_markdown="",
        data={"as_of": date(2015, 3, 1)},
        reports_root=tmp_path,
    )

    parsed = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert parsed == {"as_of": "2015-03-01"}


def test_write_report_uses_timestamped_directory(tmp_path: Path) -> None:
    fixed_time = datetime(2015, 3, 1, 12, 30, 0, tzinfo=UTC)

    directory = write_report(
        "my_job", summary_markdown="", data={}, reports_root=tmp_path, now=fixed_time
    )

    assert directory == tmp_path / "my_job" / "20150301T123000Z"


def test_write_report_never_collides_across_jobs(tmp_path: Path) -> None:
    d1 = write_report("job_a", summary_markdown="", data={}, reports_root=tmp_path)
    d2 = write_report("job_b", summary_markdown="", data={}, reports_root=tmp_path)

    assert d1 != d2
    assert d1.parent.name == "job_a"
    assert d2.parent.name == "job_b"
