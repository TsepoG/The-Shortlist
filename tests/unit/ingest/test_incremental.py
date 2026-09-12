"""Incremental per-company updates — no network (httpx.MockTransport stands in
for data.sec.gov), no DB (a fake FactWriter stands in for PostgresFactWriter).
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from shortlist.data._backends.postgres import InsertResult
from shortlist.data.types import CanonicalConcept, Cik, Fact
from shortlist.ingest.edgar_client import EdgarClient
from shortlist.ingest.incremental import update_company


class FakeFactWriter:
    def __init__(self) -> None:
        self.batches: list[Sequence[Fact]] = []

    def insert_facts(self, facts: Sequence[Fact]) -> InsertResult:
        self.batches.append(facts)
        return InsertResult(inserted=len(facts), skipped=0)


@pytest.fixture(autouse=True)
def _user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTLIST_SEC_USER_AGENT", "Shortlist tests <test@example.com>")


def test_update_company_fetches_parses_and_writes() -> None:
    cik = Cik.parse("0000320193")
    payload = {
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

    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=payload)

    writer = FakeFactWriter()
    with EdgarClient(transport=httpx.MockTransport(handler)) as client:
        result = update_company(client, writer, cik)

    assert seen_urls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]
    assert len(result.facts) == 1
    assert result.facts[0].concept is CanonicalConcept.REVENUE
    assert len(writer.batches) == 1
    assert writer.batches[0] == result.facts


def test_update_company_returns_unmapped_tags_for_accumulation() -> None:
    cik = Cik.parse("0000320193")
    payload = {
        "facts": {"acme": {"CustomThing": {"units": {"USD": [{"end": "2014-12-31", "val": 1}]}}}}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    writer = FakeFactWriter()
    with EdgarClient(transport=httpx.MockTransport(handler)) as client:
        result = update_company(client, writer, cik)

    assert result.facts == ()
    assert len(result.unmapped_tags) == 1
    assert result.unmapped_tags[0].tag == "CustomThing"
