"""Price ingestion — PHASE_2.md.

Layered the same way phase 1's EDGAR ingestion is:

- `provider.py` — the `PriceProvider` Protocol and its concrete
  implementation(s). Knows how to talk to an external source; knows nothing
  about CIKs, scope, or storage.
- `resolution.py` — which ticker a CIK traded under, and for what date range.
  The seam where a ticker (what a provider understands) becomes a CIK (what
  everything else in the system keys on).
- `loader.py` — orchestration: resolve, fetch, verify, write.

Nothing here imports from `shortlist.data._backends`; storage is reached
through `shortlist.data.factory`, exactly as phase 1's backfill does.
"""
