import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_events import previous_releases


def test_previous_release_skips_refiles_and_old_quarters():
    ev = pd.DataFrame({
        "cik": [1, 1, 1, 1, 2],
        "accession": ["a1", "a2", "a2b", "a3", "b1"],
        "accepted_utc": ["2024-01-20T21:00:00", "2024-04-20T21:00:00", "2024-04-25T12:00:00",
                         "2025-02-01T21:00:00", "2024-04-20T21:00:00"],
        "ex99_url": ["u1", "u2", "u2b", "u3", "v1"]})
    prev = previous_releases(ev)
    assert prev["a2"] == "u1"
    assert prev["a2b"] == "u1"  # 5 days after a2: an amendment of the same quarter, not the previous release
    assert "a3" not in prev  # the last one is 282 days earlier: a quarter is missing
    assert "a1" not in prev and "b1" not in prev
