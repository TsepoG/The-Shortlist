# Shortlist

A sector fundamental screener and historical backtester. See [DESIGN.md](DESIGN.md),
[TESTING.md](TESTING.md), and [CLAUDE.md](CLAUDE.md) for the full specification and
conventions. Phase specs live in [docs/phases/](docs/phases/).

This is a research and screening tool. It produces a shortlist for human diligence,
not investment advice, and backtest performance does not predict live performance.

## Setup

```
cp .env.example .env         then fill in real values — see that file's comments
uv sync                      install deps
docker compose up -d         local Postgres (reads POSTGRES_* from .env)
uv run alembic upgrade head  create/update the schema
```

`.env` is gitignored and never committed; `.env.example` documents every
variable with a placeholder. Two are required with no default —
`src/shortlist/config.py` loads `.env` automatically (via `python-dotenv`) and
raises immediately if either is missing:

- `DATABASE_URL` — must match `docker-compose.yml`'s port (5433 by default;
  see that file's comment on why it isn't 5432 on some machines) and the
  `POSTGRES_*` values in `.env`
- `SHORTLIST_SEC_USER_AGENT` — a descriptive User-Agent with a real contact
  address, required by SEC EDGAR (`PHASE_1.md` §1). Only needed for ingestion
  commands, not for the test suite.

## Commands

```
uv sync                          install deps
uv run pytest                    full suite (needs DATABASE_URL for tests/integration)
uv run pytest tests/unit         fast suite (no db/network)
uv run ruff check --fix .        lint
uv run ruff format .             format
uv run mypy src tests            types
uv run alembic upgrade head      migrations
```

`tests/integration/` skips locally if `DATABASE_URL` is unset or unreachable,
but fails (rather than skips) when `CI` is set — see `tests/integration/conftest.py`.
