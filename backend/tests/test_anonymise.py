"""Anonymised-prompt check (PLAN_60_V2): the mask removes who and when and keeps every number; the comparison."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import anon_prompt_check as A

from app.sandbox.anonymise import mask

SHEET = """Company: KEYS (Information Technology); earnings release filed 2026-08-19T20:05 UTC.
Revenue: 1,352M vs 1,217M a year earlier (+11.1%)
Diluted EPS (GAAP): 1.10 vs 2.24 a year earlier (-50.9%)
Adjusted EPS: 2.03
Guidance: raised; management tone: positive
Quote: "Keysight's fourth fiscal quarter of 2026 revenue is expected to be in the range of $1.930 billion to $1.950 billion."
Quote: "KEYS2026 priorities: Keysight Technologies closed the deal on June 30, 2026 and again on Sept. 3rd"
Stock vs its sector before the release: 20 days +3.2%, 60 days -1.0%
Earlier quarters as filed with the SEC (diluted EPS vs a year earlier):
  quarter ended 2025-07-31: EPS 2.24 vs 1.97 a year earlier, revenue 2,025M (+7.5%)"""


def test_mask_removes_who_and_when_and_keeps_the_numbers() -> None:
    m = mask(SHEET, "KEYS", "Keysight Technologies")
    lines = m.split("\n")
    assert lines[0] == "Company: XXXX (Information Technology); earnings release."
    assert lines[1:5] == SHEET.split("\n")[1:5] and lines[7] == SHEET.split("\n")[7]  # numbers untouched
    assert lines[5] == ('Quote: "the company\'s fourth fiscal quarter of [year] revenue is expected to be in the '
                        'range of $1.930 billion to $1.950 billion."')
    assert lines[6] == 'Quote: "XXXX[year] priorities: the company closed the deal on [date], [year] and again on [date]"'
    assert lines[9] == "  quarter ended [year]-07-31: EPS 2.24 vs 1.97 a year earlier, revenue 2,025M (+7.5%)"
    for leak in ("KEYS", "Keysight", "2026", "2025-", "June"):
        assert leak not in m


def test_ordinary_word_names_keep_the_quotes_wording() -> None:
    q = 'Company: TGT (Consumer Staples); earnings release filed 2025-03-04T11:30 UTC.\nQuote: "Target met its target; TARGET CORPORATION and American demand"'
    assert mask(q, "TGT", "Target").split("\n")[1] == 'Quote: "the company met its target; the company CORPORATION and American demand"'
    # "American" as a first word is not masked by itself; the full name is
    q2 = 'Company: AXP (Financials); earnings release.\nQuote: "American Express grew; American consumers spent"'
    assert mask(q2, "AXP", "American Express").split("\n")[1] == 'Quote: "the company grew; American consumers spent"'
    assert mask("Company: BRK.B (Financials); earnings release.\nQuote: \"BRK-B rose\"", "BRK.B", None) == (
        'Company: XXXX (Financials); earnings release.\nQuote: "XXXX rose"')


def _frame(n_months: int = 6, per: int = 40, masked_noise: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(n_months):
        f = rng.normal(size=per)
        lo = f + rng.normal(size=per)  # the unmasked score has a real edge
        for i in range(per):
            rows.append({"accession": f"{m}-{i}", "sample": "2024" if m < 3 else "2025-26", "month": f"2025-{m + 1:02d}",
                         "logodds": lo[i], "logodds_masked": lo[i] if masked_noise == 0 else rng.normal(), "fwd5": f[i]})
    return pd.DataFrame(rows)


def test_compare_identical_scores_show_no_memory_and_noise_is_flagged() -> None:
    same = A.compare(_frame())
    assert same["months"] == 6 and same["d_mean"] == 0 and same["verdict"] == "no evidence of memory"
    assert same["rank_corr_masked_vs_unmasked"] == 1.0 and same["call_flips_pct"] == 0.0
    assert same["ic_unmasked"] > 0.4 and set(same["by_sample"]) == {"2024", "2025-26"}
    lost = A.compare(_frame(masked_noise=1.0))  # masking destroyed the edge: the masked IC is near 0
    assert lost["d_ci95"][1] < 0 and lost["verdict"] == "memory flag"
    assert A.compare(_frame(n_months=1))["verdict"] == "not enough months"


def test_score_is_resumable_and_asks_the_judges_question(tmp_path: Path) -> None:
    asked = []

    async def llm(system: str, user: str, mode: str | None = None) -> str:
        asked.append((system, user, mode))
        return json.dumps({"logodds": 1.5, "mass": 0.9, "censored": False})
    rows = [{"accession": str(i), "ticker": "AAA", "sample": "2024", "logodds": 0.0, "user": f"sheet {i}"} for i in range(5)]
    out = tmp_path / "anon.jsonl"
    assert asyncio.run(A.score(llm, rows[:2], out)) == 2
    assert asyncio.run(A.score(llm, rows[2:], out)) == 3
    got = [json.loads(x) for x in out.read_text().splitlines()]
    assert [g["accession"] for g in got] == ["0", "1", "2", "3", "4"] and got[0]["logodds_masked"] == 1.5
    assert all(m == "buypass_lo" and "next 5 trading days" in s and "BUY or PASS" in s for s, _, m in asked)
