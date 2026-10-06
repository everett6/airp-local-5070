"""Exact prospective sleeve inputs, independent of later price-cache revisions."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

import pandas as pd

from app.portfolio import sleeve

PAIR_FIELDS = {"accession", "ticker", "etf", "logodds", "entry_day", "entry_deadline", "qty", "etf_qty",
               "status", "note", "entry_index_day", "entry_open", "etf_entry_open", "exit_day",
               "exit_open", "etf_exit_open", "pnl", "ret"}


def state(book: dict[str, Any]) -> dict[str, Any]:
    result = {k: copy.deepcopy(book[k]) for k in ("cash", "peak", "equity", "threshold", "score", "seen", "history")}
    result["pairs"] = [{k: copy.deepcopy(v) for k, v in p.items() if k in PAIR_FIELDS} for p in book["pairs"]]
    return result


def frame(data: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(data["data"], columns=data["columns"], index=pd.to_datetime(data["index"])).astype(float)


def inputs(before: dict[str, Any], after: dict[str, Any], decisions: list[dict[str, Any]],
           opens: pd.DataFrame, closes: pd.DataFrame, now: datetime, mode: str) -> dict[str, Any]:
    return {"as_of": now.isoformat(), "mode": mode, "before": state(before), "after": state(after),
            "decisions": decisions, "opens": json.loads(opens.to_json(orient="split", date_format="iso", double_precision=15)),
            "closes": json.loads(closes.to_json(orient="split", date_format="iso", double_precision=15))}


def replay(rows: list[dict[str, Any]], etf_of: dict[str, str], scores: dict[str, float] | None = None) -> dict[str, Any]:
    if not rows:
        raise ValueError("No prospective sleeve journal")
    book: dict[str, Any] = copy.deepcopy(rows[0]["before"])
    for row in rows:
        if scores is None and state(book) != row["before"]:
            raise ValueError("Sleeve journal has a missing step or changed starting state")
        decisions = row["decisions"] if scores is None else [
            {**d, "logodds": scores.get(d["accession"], d.get("logodds"))} if d.get("type") == "decision" else d
            for d in row["decisions"]]
        sleeve.step(book, decisions, frame(row["opens"]), frame(row["closes"]), etf_of,
                    datetime.fromisoformat(row["as_of"]), row["mode"],
                    set(row["allowed_stocks"]) if row.get("allowed_stocks") is not None else None)
        if scores is None and state(book) != row["after"]:
            raise ValueError("Sleeve journal output does not reproduce")
    return book
