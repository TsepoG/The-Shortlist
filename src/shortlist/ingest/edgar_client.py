"""HTTP access to SEC EDGAR, with PHASE_1.md §1's compliance rules built in
rather than left to be remembered at each call site:

- a descriptive `User-Agent` with a contact address (`SHORTLIST_SEC_USER_AGENT`,
  required — see `shortlist.config`)
- rate limiting to <=10 requests/second
- exponential backoff on 403/429
- explicit timeouts

`data.sec.gov` does not support CORS (`DESIGN.md` §4.1) — this is why ingestion
is a server-side client, not a browser fetch, and it is not a limitation to work
around.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from shortlist.config import sec_user_agent

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
COMPANY_CONCEPT_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{namespace}/{tag}.json"
)
BULK_COMPANYFACTS_ARCHIVE_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/companyfacts.zip"
)

_MAX_REQUESTS_PER_SECOND = 10
_MIN_INTERVAL_SECONDS = 1.0 / _MAX_REQUESTS_PER_SECOND
_MAX_RETRIES = 5
_INITIAL_BACKOFF_SECONDS = 1.0
_TIMEOUT_SECONDS = 30.0
_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MiB, for the bulk archive download


class EdgarClient:
    """A rate-limited, compliant HTTP client for SEC EDGAR.

    One instance should be shared across a whole ingestion run so the rate
    limiter's clock is shared too — a fresh instance per request would defeat
    the ≤10 req/s budget PHASE_1.md §1 requires.
    """

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        # sec_user_agent() raises MissingConfigError with no default, per
        # docs/phases/PHASE_1_NOTES.md: this codebase will not send a request to
        # an external service under an identity it made up.
        headers = {"User-Agent": sec_user_agent()}
        self._client = httpx.Client(headers=headers, timeout=_TIMEOUT_SECONDS, transport=transport)
        self._last_request_at: float = 0.0

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _MIN_INTERVAL_SECONDS:
            time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str) -> httpx.Response:
        backoff = _INITIAL_BACKOFF_SECONDS
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            response = self._client.get(url)
            if response.status_code in (403, 429):
                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} from {url}",
                    request=response.request,
                    response=response,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise last_exc
            response.raise_for_status()
            return response
        assert last_exc is not None  # pragma: no cover - loop always returns or raises
        raise last_exc

    def get_json(self, url: str) -> dict[str, Any]:
        response = self._get(url)
        data: dict[str, Any] = response.json()
        return data

    def get_company_facts(self, cik_10digit: str) -> dict[str, Any]:
        return self.get_json(COMPANY_FACTS_URL_TEMPLATE.format(cik=cik_10digit))

    def get_company_tickers(self) -> dict[str, Any]:
        return self.get_json(COMPANY_TICKERS_URL)

    def download_bulk_companyfacts_archive(self, destination: Path) -> Path:
        """Stream the full `companyfacts.zip` to `destination`. Resumable in the
        sense that a partial file can simply be re-downloaded — PHASE_1.md §1
        says use this archive for the initial backfill, not per-company calls;
        it is large, so this never buffers the whole thing in memory.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._throttle()
        with self._client.stream("GET", BULK_COMPANYFACTS_ARCHIVE_URL) as response:
            response.raise_for_status()
            with destination.open("wb") as f:
                for chunk in response.iter_bytes(_STREAM_CHUNK_SIZE):
                    f.write(chunk)
        return destination


def iter_bulk_companyfacts(archive_path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Stream `(filename, parsed_json)` pairs out of a downloaded bulk archive,
    one company at a time — never the whole ~18k-file archive in memory at once.
    """
    import json
    import zipfile

    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".json"):
                continue
            with archive.open(name) as member:
                yield name, json.load(member)
