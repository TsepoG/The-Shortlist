"""Unit tests for the EDGAR client — no network. Uses `httpx.MockTransport` to
stand in for `data.sec.gov`/`www.sec.gov` per PHASE_1.md §1's compliance rules:
User-Agent, retry-on-429/403, and URL construction.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest

from shortlist.config import MissingConfigError
from shortlist.ingest.edgar_client import EdgarClient, iter_bulk_companyfacts


@pytest.fixture(autouse=True)
def _user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTLIST_SEC_USER_AGENT", "Shortlist tests <test@example.com>")


def test_client_construction_requires_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHORTLIST_SEC_USER_AGENT", raising=False)

    with pytest.raises(MissingConfigError):
        EdgarClient()


def test_client_sends_configured_user_agent() -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json={"ok": True})

    with EdgarClient(transport=httpx.MockTransport(handler)) as client:
        client.get_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")

    assert seen_headers == ["Shortlist tests <test@example.com>"]


def test_client_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shortlist.ingest.edgar_client.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    with EdgarClient(transport=httpx.MockTransport(handler)) as client:
        result = client.get_json("https://data.sec.gov/anything")

    assert result == {"ok": True}
    assert attempts["count"] == 3


def test_client_raises_after_exhausting_retries_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shortlist.ingest.edgar_client.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    with (
        EdgarClient(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        client.get_json("https://data.sec.gov/anything")


def test_client_does_not_retry_other_error_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shortlist.ingest.edgar_client.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500)

    with (
        EdgarClient(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        client.get_json("https://data.sec.gov/anything")

    assert attempts["count"] == 1  # no retry loop for a plain 500


def test_get_company_facts_builds_correct_url() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"cik": 320193})

    with EdgarClient(transport=httpx.MockTransport(handler)) as client:
        client.get_company_facts("0000320193")

    assert seen_urls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]


def test_iter_bulk_companyfacts_yields_each_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("CIK0000320193.json", json.dumps({"cik": 320193}))
        zf.writestr("CIK0000789019.json", json.dumps({"cik": 789019}))
        zf.writestr("README.txt", "not json, must be skipped")

    results = list(iter_bulk_companyfacts(archive_path))

    names = [name for name, _ in results]
    assert names == ["CIK0000320193.json", "CIK0000789019.json"]
    assert results[0][1] == {"cik": 320193}
