# Shortlist

A sector fundamental screener and historical backtester. See [DESIGN.md](DESIGN.md),
[TESTING.md](TESTING.md), and [CLAUDE.md](CLAUDE.md) for the full specification and
conventions. Phase specs live in [docs/phases/](docs/phases/).

This is a research and screening tool. It produces a shortlist for human diligence,
not investment advice, and backtest performance does not predict live performance.

## Commands

```
uv sync                      install deps
uv run pytest                full suite
uv run pytest tests/unit     fast suite (no db/network)
uv run ruff check --fix .    lint
uv run mypy src              types
```
