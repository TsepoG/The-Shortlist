"""Shared pytest configuration.

No fixtures live here yet — phase 0's fakes are simple enough to construct
directly in each test via `tests.fakes`. This file exists so `tests/` is
unambiguously the pytest rootdir-relative package root, matching
`pythonpath = ["."]` in `pyproject.toml`.
"""
