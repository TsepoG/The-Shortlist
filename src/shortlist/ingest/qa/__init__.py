"""Data quality jobs: reconciliation, coverage, and the unmapped-tag report.

Run over the whole ingested universe, producing archived report artifacts
rather than console output (`TESTING.md` §7). Each job's *logic* is a pure
function, unit-tested against synthetic facts with no database; the thin CLI
wrapper that fetches from Postgres and calls `report.write_report` is what
actually runs on a schedule.
"""
