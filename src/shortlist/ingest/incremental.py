"""Per-company incremental updates, for after the bulk backfill has run.

PHASE_1.md §1: "Use the per-company API for incremental updates afterward."
Same parser (`companyfacts.py`), same writer contract (`backfill.FactWriter`),
same idempotency — the only difference from the backfill path is *where* the
JSON comes from (one `EdgarClient.get_company_facts` call instead of a zip
member) and that it operates on one company at a time rather than scanning a
whole archive.
"""

from __future__ import annotations

from shortlist.data.types import Cik
from shortlist.ingest.backfill import FactWriter
from shortlist.ingest.companyfacts import ParseResult, parse_companyfacts
from shortlist.ingest.edgar_client import EdgarClient


def update_company(client: EdgarClient, writer: FactWriter, cik: Cik) -> ParseResult:
    """Fetch, parse, and write the current `companyfacts` for one company.

    Returns the `ParseResult` so a caller can accumulate unmapped tags and
    rejections across a batch of companies the same way `backfill.py` does —
    the summary bookkeeping in `backfill.BackfillSummary` is written against
    `ParseResult`, not against the backfill path specifically, so it applies
    here unchanged.
    """
    payload = client.get_company_facts(cik.value)
    result = parse_companyfacts(cik, payload)
    writer.insert_facts(result.facts)
    return result
