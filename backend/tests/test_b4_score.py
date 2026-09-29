"""Arm B4's scoring (scripts/llm_fields_test.py b4_score), synthetic data only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llm_fields import FIELDS, FIELDS_R
from llm_fields_test import b4_score


def frame(n: int, months: int, signal: bool, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"eps_q": rng.normal(1, 0.3, n), "eps_prior": 1.0, "rev_q": rng.normal(100, 5, n),
                       "rev_prior": 100.0, "month": rng.integers(0, months, n), "index": rng.choice(["sp400", "sp600"], n)})
    for f, (_, default) in FIELDS.items():
        df[f] = default
    key, (labels, default) = next(iter(FIELDS_R.items()))
    other = next(v for v in labels if v != default)
    for f, (_, d) in FIELDS_R.items():
        df[f"{f}_r"] = d
    hit = rng.random(n) < 0.5
    df.loc[hit, f"{key}_r"] = other
    noise = rng.normal(0, 0.05, n)
    for h in ("fwd5", "fwd20"):
        df[h] = noise + (0.05 * hit if signal else 0.0)
    return df


def test_b4_passes_only_when_the_research_field_carries_the_return():
    out = b4_score(frame(3000, 12, True, 0), frame(2400, 8, True, 1))
    assert out["pass"] and out["fwd20"]["B2_minus_A"]["ci_lo"] > 0
    assert set(out["fwd20"]["by_index"]) == {"sp400", "sp600"}
    assert not b4_score(frame(3000, 12, False, 0), frame(2400, 8, False, 1))["pass"]
