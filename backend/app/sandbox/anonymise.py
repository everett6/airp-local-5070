"""Mask who and when in a judge fact sheet (docs/PLAN_60_V2.md "Anonymised-prompt check", spec fixed 2026-10-01).

Bonsai may have read what happened to these companies. A masked sheet keeps every number, growth rate, guidance, tone
and the wording of the quotes, and removes what would let the model recognise the company and the date:
1. the ticker becomes XXXX (whole word);
2. the first line's filing time is dropped;
3. in quoted lines, the company's name (and its first word, if 4+ letters and not a common word) becomes
   "the company";
4. years 1990-2039 become [year]; a month name followed by a day number becomes [date].
"""
from __future__ import annotations

import re

# ordinary English words that open company names: as a first word they are not masked by themselves (rule 3), and a
# one-word name among them ("Target") is masked only when capitalised, so "our target" keeps its wording
COMMON = frozenset("""american general first united national international global digital public home dollar target
best service western southern northern eastern central universal capital advanced applied bank state union delta
progressive realty host news match live fair ball block builders principal regions paramount world waste republic
federal cardinal quest dominion consolidated southwest constellation discover citizens fifth everest travelers globe
brown marsh church dover parker snap stanley fortune carrier pool tractor genuine advance charter electronic take
royal carnival booking extra equity invitation crown tower iron texas pacific atlantic north south east west city
old new mid air gap""".split())  # noqa: SIM905
_MONTH = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
          r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?")
_DATE = re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b")
_YEAR = re.compile(r"(?<![\d,.])(?:199\d|20[0-3]\d)(?![\d,]|\.\d|M\b)")
_FILED = re.compile(r"(earnings release) filed \S+ UTC")


def _word(w: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", re.IGNORECASE)


def name_patterns(name: str | None) -> list[re.Pattern[str]]:
    """The full name first, then its first word when that word is distinctive."""
    name = (name or "").strip()
    if len(name) < 2:
        return []
    if name.lower() in COMMON:  # capitalised or upper case only
        return [re.compile(rf"(?<![A-Za-z0-9])(?:{re.escape(name)}|{re.escape(name.upper())})(?![A-Za-z0-9])")]
    pats = [_word(name)]
    first = re.split(r"[\s,.&]+", name)[0]
    if first != name and len(first) >= 4 and first.lower() not in COMMON:
        pats.append(_word(first))
    return pats


def mask(sheet: str, ticker: str, name: str | None = None) -> str:
    tickers = {ticker, ticker.replace(".", "-"), ticker.replace("-", ".")}
    # a digit may follow ("EFX2026 priorities"), a letter may not
    tick = [re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z])") for t in sorted(tickers, key=len, reverse=True)
            if t]
    names = name_patterns(name)
    out = []
    for i, line in enumerate(sheet.split("\n")):
        if i == 0:
            line = _FILED.sub(r"\1", line)
        if line.startswith("Quote:"):
            for p in names:
                line = p.sub("the company", line)
        for p in tick:
            line = p.sub("XXXX", line)
        line = _DATE.sub("[date]", line)
        out.append(_YEAR.sub("[year]", line))
    return "\n".join(out)
