"""The unmapped-tag report — PHASE_1.md §6.

The counting itself already happens during parsing (`companyfacts.py`'s
`unmapped_tags`, accumulated across a run by `backfill.BackfillSummary`); this
module only turns that into the report artifact and the ranking PHASE_1.md §6
asks for: "Every raw tag encountered that no alias chain claimed, ranked by
frequency ... the primary input for extending the alias lists."

A tag in `derive.DERIVATION_INPUT_TAGS` (e.g.
`LiabilitiesAndStockholdersEquity`) is genuinely unclaimed by any alias chain,
so its count stays truthful here — but it is *consumed* by a derivation
(`derive.py`), not simply missing an alias, so `render_markdown` annotates it
rather than leaving it to read as an untriaged gap in "the primary input for
extending the alias lists."
"""

from __future__ import annotations

from collections.abc import Sequence

from shortlist.ingest.companyfacts import UnmappedTag
from shortlist.ingest.derive import DERIVATION_INPUT_TAGS
from shortlist.ingest.qa.report import write_report

# A tag seen at least this often is worth triaging first — PHASE_1.md §6: "a
# high-frequency unmapped tag is usually a missing alias, not a genuinely
# exotic concept." The threshold itself is a triage convenience, not a spec
# value; PHASE_1.md names no number, so this is stated here rather than
# invented into the alias lists themselves.
HIGH_FREQUENCY_THRESHOLD = 5


def high_frequency_tags(
    tags: Sequence[UnmappedTag], threshold: int = HIGH_FREQUENCY_THRESHOLD
) -> tuple[UnmappedTag, ...]:
    return tuple(t for t in tags if t.count >= threshold)


def render_markdown(tags: Sequence[UnmappedTag]) -> str:
    lines = ["# Unmapped tag report", ""]
    if not tags:
        lines.append("No unmapped tags encountered.")
        return "\n".join(lines) + "\n"

    high_frequency = high_frequency_tags(tags)
    lines.append(
        f"{len(tags)} unmapped (namespace, tag) pairs; "
        f"{len(high_frequency)} at or above the high-frequency threshold "
        f"({HIGH_FREQUENCY_THRESHOLD}+) and worth triaging first."
    )
    lines.append("")
    lines.append("| namespace | tag | count |")
    lines.append("|---|---|---|")
    for t in tags:
        note = (
            " (consumed as a derivation input — not a missing alias)"
            if (t.namespace, t.tag) in DERIVATION_INPUT_TAGS
            else ""
        )
        lines.append(f"| {t.namespace} | {t.tag}{note} | {t.count} |")
    return "\n".join(lines) + "\n"


def write_unmapped_tags_report(tags: Sequence[UnmappedTag]) -> None:
    data = [{"namespace": t.namespace, "tag": t.tag, "count": t.count} for t in tags]
    write_report("unmapped_tags", summary_markdown=render_markdown(tags), data=data)
