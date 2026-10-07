"""O1 #28: do E1's features mean the same on the live IEX feed as on the SIP bars the model was trained on?

    python scripts/feed_check.py [--sessions 10]

For recent sessions, builds the features from SIP bars and from IEX bars (same session stats from SIP history) and
reports, per feature, the correlation and the ratio of standard deviations at the decision bars. Writes
results/forward/algo/feed_check.json (shown beside Autopilot Day's label). Read-only market data; no orders.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np

from app.sandbox import minute_ensemble as me


async def main(sessions: int) -> None:
    import algo_engine as ae
    eng = ae.Engine()
    end = datetime.now(UTC) - timedelta(minutes=20)  # SIP's free tier: nothing from the last 15 minutes
    start = end - timedelta(days=sessions * 7 // 5 + 40)
    sip = await eng.bars(start, end, "sip")
    iex = await eng.bars(start, end, "iex")
    await eng.http.aclose()
    days = sorted(set(sip["SPY"]["ts"].dt.date))[-sessions:]
    rows: dict[str, dict[str, list[float]]] = {f: {"sip": [], "iex": []} for f in me.FEATURES}
    for s in me.UNIVERSE:
        lead = me.leader_of(s)
        fs, rolled = me.frame(sip[s], sip[lead], with_rolled=True)
        stats = rolled.shift(1)
        fi = me.frame(iex[s], iex[lead], stats=stats)
        both = fs.join(fi, lsuffix="_sip", rsuffix="_iex", how="inner")
        both = both[both["m_sip"].isin(me.DECISION_BARS) & both["date_sip"].isin(days)]
        for f in me.FEATURES:
            ok = np.isfinite(both[f + "_sip"]) & np.isfinite(both[f + "_iex"])
            rows[f]["sip"] += both.loc[ok, f + "_sip"].tolist()
            rows[f]["iex"] += both.loc[ok, f + "_iex"].tolist()
    out = {}
    for f, v in rows.items():
        a, b = np.array(v["sip"]), np.array(v["iex"])
        if len(a) < 30 or a.std() == 0 or b.std() == 0:
            out[f] = {"n": len(a), "corr": None, "std_ratio": None}
            continue
        out[f] = {"n": len(a), "corr": round(float(np.corrcoef(a, b)[0, 1]), 3),
                  "std_ratio": round(float(b.std() / a.std()), 3)}
    bad = [f for f, x in out.items() if x["corr"] is not None and x["corr"] < 0.8]
    res = {"at": datetime.now(UTC).isoformat(), "sessions": [str(d) for d in days], "features": out,
           "mismatched": bad,
           "verdict": ("features differ between the live IEX feed and the SIP training data: " + ", ".join(bad))
           if bad else "the live IEX features track the SIP training features (correlation >= 0.8 for all)"}
    (ae.ALGO / "feed_check.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", type=int, default=10)
    asyncio.run(main(ap.parse_args().sessions))
