"""Environment configuration, resolved in one place.

Both values below are required with no fallback default: an unset `DATABASE_URL`
or `SHORTLIST_SEC_USER_AGENT` should fail loudly at startup, not silently point at
something unintended (a stray local database) or send a non-compliant request to
SEC EDGAR.

`load_dotenv()` runs once, here, at import time — this module is the one place
every env read in the codebase goes through (alembic/env.py, every ingest
script, every test that needs `DATABASE_URL`), so loading `.env` here means it
takes effect regardless of how the process was started (`uv run pytest`,
`uv run alembic`, a bare `python -m ...`). It never overrides a variable
already set in the real environment (python-dotenv's default), so CI — which
sets `DATABASE_URL` directly via the workflow's `env:` block and has no `.env`
file at all — is unaffected either way.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class MissingConfigError(RuntimeError):
    """A required environment variable was not set."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} is required and was not set. See README.md for setup.")


def database_url() -> str:
    """The Postgres connection string. No default — see `docker-compose.yml` for
    the local value, which is deliberately identical to `.github/workflows/ci.yml`'s
    service block.
    """
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise MissingConfigError("DATABASE_URL")
    return value


def sec_user_agent() -> str:
    """The `User-Agent` sent on every SEC EDGAR request, per PHASE_1.md §1's
    compliance rules: a descriptive value with a real contact address. This is
    never hardcoded — the address is the caller's, not this codebase's, to supply.
    """
    value = os.environ.get("SHORTLIST_SEC_USER_AGENT")
    if not value:
        raise MissingConfigError("SHORTLIST_SEC_USER_AGENT")
    return value


def is_ci() -> bool:
    """True when running under CI (GitHub Actions sets `CI=true`).

    Used only to decide whether an unreachable integration database should skip
    (local convenience) or fail (CI must not silently pass PHASE_1.md §7's
    required check).
    """
    return os.environ.get("CI", "").lower() in {"1", "true"}
