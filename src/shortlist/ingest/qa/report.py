"""Shared report-writing for the QA jobs.

`TESTING.md` §7: "Every data quality report and backtest run should be
archived, not just printed." Each job gets a timestamped directory under
`reports/` (gitignored — CLAUDE.md forbids committing data dumps) holding a
machine-readable `report.json` and a human-readable `report.md`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_REPORTS_ROOT = Path("reports")


def _default_json(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_report(
    job_name: str,
    *,
    summary_markdown: str,
    data: object,
    reports_root: Path = DEFAULT_REPORTS_ROOT,
    now: datetime | None = None,
) -> Path:
    """Write `data` as JSON and `summary_markdown` as Markdown into a new
    timestamped directory under `reports_root/job_name/`. Returns that
    directory's path.
    """
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    directory = reports_root / job_name / timestamp
    directory.mkdir(parents=True, exist_ok=True)

    (directory / "report.json").write_text(
        json.dumps(data, default=_default_json, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (directory / "report.md").write_text(summary_markdown, encoding="utf-8")

    return directory
