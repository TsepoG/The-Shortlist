"""The phase 0 point-in-time contract, defined once and run against every backend.

`fact_repository_contract.py` holds the PHASE_0.md §6 scenarios as plain functions
taking a `make_repo` factory. `tests/unit/data/` calls them against the in-memory
fake (no DB, no network); `tests/integration/` calls the identical functions
against `PostgresFactRepository`, per PHASE_1.md §7's requirement that "Phase 0
guard tests pass unchanged against the PostgreSQL implementation."

Extracting this once — rather than duplicating the scenarios into a parallel
integration module — is deliberate: two definitions of one invariant can drift
apart silently, which is the exact failure mode this contract exists to prevent.
"""
