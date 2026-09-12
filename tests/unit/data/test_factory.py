"""PHASE_0.md §7 — storage is stubbed; no PostgreSQL dependency yet.

`create_repositories` is the public seam phase 1 wires a real backend into. Phase
0 proves the seam exists and clearly refuses to fake a backend that isn't built.
"""

import pytest

from shortlist.data.factory import Backend, create_repositories


def test_create_repositories_postgres_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        create_repositories(Backend.POSTGRES)
