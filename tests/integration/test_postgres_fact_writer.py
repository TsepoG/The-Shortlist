"""`PostgresFactWriter.insert_facts`'s reported counts, against real Postgres.

Regression coverage for a defect found running the real phase 1 backfill:
`result.rowcount` on an `INSERT ... ON CONFLICT DO NOTHING` was found to be
unconditionally `-1` ("cannot be determined") under psycopg3 — including when
some or all rows in the batch were genuinely new. The underlying writes were
always correct; only the self-reported `InsertResult` counts were wrong,
silently, in every direction from "off by one" to "negative". Fixed by
counting `RETURNING id` rows instead of trusting `rowcount`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Connection

from shortlist.data._backends.postgres import PostgresFactWriter
from shortlist.data.types import CanonicalConcept
from tests.fakes.builders import fact

pytestmark = pytest.mark.integration

_CIK = "0000000001"


def test_insert_facts_reports_all_new_rows_as_inserted(pg_connection: Connection) -> None:
    writer = PostgresFactWriter(pg_connection)
    facts = tuple(
        fact(
            cik=_CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000 + i,
            period_end="2014-12-31",
            filed_date="2015-02-15",
            accession_number=f"acc-{i}",
        )
        for i in range(5)
    )

    result = writer.insert_facts(facts)

    assert result.inserted == 5
    assert result.skipped == 0


def test_insert_facts_reports_zero_inserted_when_everything_already_conflicts(
    pg_connection: Connection,
) -> None:
    writer = PostgresFactWriter(pg_connection)
    facts = tuple(
        fact(
            cik=_CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000 + i,
            period_end="2014-12-31",
            filed_date="2015-02-15",
            accession_number=f"acc-{i}",
        )
        for i in range(5)
    )
    writer.insert_facts(facts)  # first call: all genuinely new

    result = writer.insert_facts(facts)  # second call: every row now conflicts

    assert result.inserted == 0
    assert result.skipped == 5


def test_insert_facts_correctly_counts_a_mixed_batch(pg_connection: Connection) -> None:
    # The exact scenario that exposed the bug: some rows in the batch already
    # exist, some are genuinely new, in the SAME insert_facts() call. A
    # rowcount-based count reported -1 here even though the write itself
    # (5 new rows landing) was correct.
    writer = PostgresFactWriter(pg_connection)
    already_present = tuple(
        fact(
            cik=_CIK,
            concept=CanonicalConcept.REVENUE,
            value=1000 + i,
            period_end="2014-12-31",
            filed_date="2015-02-15",
            accession_number=f"existing-{i}",
        )
        for i in range(5)
    )
    writer.insert_facts(already_present)

    genuinely_new = tuple(
        fact(
            cik=_CIK,
            concept=CanonicalConcept.REVENUE,
            value=2000 + i,
            period_end="2015-12-31",
            filed_date="2016-02-15",
            accession_number=f"new-{i}",
        )
        for i in range(5)
    )
    mixed = already_present + genuinely_new

    result = writer.insert_facts(mixed)

    assert result.inserted == 5
    assert result.skipped == 5


def test_insert_facts_sums_correctly_across_multiple_batches(
    pg_connection: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force a small batch size so a handful of rows still exercises the
    # multi-batch accumulation path (_INSERT_BATCH_SIZE is 1000 in
    # production — this proves the per-batch counts sum correctly without
    # constructing a real 1000+ row fixture).
    monkeypatch.setattr("shortlist.data._backends.postgres._INSERT_BATCH_SIZE", 2)
    writer = PostgresFactWriter(pg_connection)
    facts = tuple(
        fact(
            cik=_CIK,
            concept=CanonicalConcept.REVENUE,
            value=3000 + i,
            period_end="2016-12-31",
            filed_date="2017-02-15",
            accession_number=f"batch-{i}",
        )
        for i in range(7)  # 4 batches of size 2, 2, 2, 1
    )

    result = writer.insert_facts(facts)

    assert result.inserted == 7
    assert result.skipped == 0
