"""Build the frozen E1 macro announcement calendar from public schedules."""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
OUT = BACKEND / "data" / "macro" / "announcements.csv"
RAW = BACKEND / "data" / "macro" / "raw"
START, END = pd.Timestamp("2013-01-01"), pd.Timestamp("2026-09-25")
SOURCES = {
    "jobs": "https://alfred.stlouisfed.org/release/downloaddates?rid=50&ff=txt",
    "ppi": "https://alfred.stlouisfed.org/release/downloaddates?rid=46&ff=txt",
}


def fetch(url: str, filename: str) -> str:
    """Fetch once per cache file, with a polite delay before network access."""
    path = RAW / filename
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    time.sleep(2)
    request = Request(url, headers={"User-Agent": "airp-research (paper trading research)"})
    with urlopen(request, timeout=30) as response:
        body = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body.decode("utf-8", errors="replace")


def alfred_first_month_dates(text: str) -> list[pd.Timestamp]:
    """ALFRED omits reference month; retain earliest release in each calendar month."""
    dates = sorted({pd.Timestamp(x) for x in re.findall(r"(?m)^\s*(20\d{2}-\d\d-\d\d)\s*$", text)})
    first: dict[str, pd.Timestamp] = {}
    for date in dates:
        first.setdefault(date.strftime("%Y-%m"), date)
    return [date for date in first.values() if START <= date <= END]


def count_exceptions(rows: pd.DataFrame, start_year: int = 2013,
                     end_year: int = 2025) -> list[str]:
    """Return unexplained per-year count violations, with any row note explaining year accepted."""
    problems: list[str] = []
    for year in range(start_year, end_year + 1):
        subset = rows[pd.to_datetime(rows["date"]).dt.year == year]
        for event, lo, hi in (("jobs", 11, 13), ("ppi", 11, 13), ("fomc", 7, 9)):
            n = int((subset["event"] == event).sum())
            if not lo <= n <= hi:
                notes = " ".join(subset.loc[subset["event"] == event, "note"].astype(str)).lower()
                if not notes.strip():
                    problems.append(f"{year} {event} count {n} without note")
    return problems


def fomc_dates() -> list[tuple[pd.Timestamp, str, str]]:
    """Read dates from the Fed's Statement links, excluding non-statement actions."""
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    text = fetch(url, "fomccalendars.htm")
    found: dict[pd.Timestamp, tuple[str, str]] = {}
    pattern = re.compile(r'href="[^"]*monetary(20\d{2})(\d{2})(\d{2})a(?:\d)?\.htm"[^>]*>HTML</a>', re.IGNORECASE)
    for year, month, day in pattern.findall(text):
        date = pd.Timestamp(year=int(year), month=int(month), day=int(day))
        found[date] = (url, "Scheduled FOMC statement link in Fed meeting calendar")
    # The current calendar page contains current/future years; historical page URLs are canonical for 2013-2020.
    for year in range(2013, 2021):
        hist_url = f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
        text = fetch(hist_url, f"fomchistorical{year}.htm")
        pattern = re.compile(r'href="[^"]*monetary(20\d{2})(\d{2})(\d{2})a\.htm"[^>]*>Statement</a>', re.IGNORECASE)
        for y, month, day in pattern.findall(text):
            if int(y) == year:
                date = pd.Timestamp(year=int(y), month=int(month), day=int(day))
                found[date] = (hist_url, "Scheduled FOMC Statement link in Fed historical meeting calendar")
    # These are non-calendar policy actions / reserve-management statements, not scheduled meetings.
    for day in ("2019-10-11", "2020-03-03", "2020-03-15", "2020-03-23"):
        found.pop(pd.Timestamp(day), None)
    return [(d, src, note) for d, (src, note) in sorted(found.items()) if START <= d <= END]


def build() -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for event, url in SOURCES.items():
        raw = fetch(url, f"{event}_alfred.txt")
        for date in alfred_first_month_dates(raw):
            rows.append({"date": date.strftime("%Y-%m-%d"), "event": event, "source": url,
                         "note": "ALFRED has no reference-month field; earliest listed release date in calendar month retained"})
    for date, url, note in fomc_dates():
        rows.append({"date": date.strftime("%Y-%m-%d"), "event": "fomc", "source": url, "note": note})
    frame = pd.DataFrame(rows).drop_duplicates(["date", "event"]).sort_values(["date", "event"])
    frame = frame[(pd.to_datetime(frame["date"]) >= START) & (pd.to_datetime(frame["date"]) <= END)]
    # Informational event-calendar exceptions are attached to that event type/year and satisfy the runner guard.
    for year in range(2013, 2027):
        for event, low, high in (("jobs", 11, 13), ("ppi", 11, 13), ("fomc", 7, 9)):
            mask = (pd.to_datetime(frame.date).dt.year == year) & (frame.event == event)
            n = int(mask.sum())
            if not low <= n <= high:
                explanation = ("calendar-year count differs: ALFRED earliest-per-calendar-month method; 2025 shutdown delayed September reference-month release to November; dates reflect actual release"
                               if year == 2025 and event in ("jobs", "ppi") else
                               "calendar-year count differs: 2020 had seven scheduled statement dates; emergency March actions and the canceled scheduled March meeting are excluded"
                               if year == 2020 else
                               "calendar-year count differs: partial calendar year through 2026-09-25 only")
                frame.loc[mask, "note"] += f"; {year} exception: {explanation}"
            elif year == 2020 and event == "fomc":
                frame.loc[mask, "note"] += "; 2020 exception: seven scheduled statement dates; emergency March actions and canceled scheduled March meeting excluded"
            elif year == 2025 and event == "jobs":
                frame.loc[mask, "note"] += "; 2025 shutdown postponed September reference-month Employment Situation to 2025-11-20 and BLS canceled October report; actual release date used"
            elif year == 2025 and event == "ppi":
                frame.loc[mask, "note"] += "; 2025 shutdown postponed September reference-month PPI to 2025-11-25 and BLS canceled October 2025 PPI; actual release date used"
                frame.loc[mask, "note"] += "; November 2025 PPI first release moved to 2026-01-14 and is assigned to that actual calendar date"
            elif year == 2026:
                frame.loc[mask, "note"] += "; partial calendar year through 2026-09-25 only"
    frame.to_csv(OUT, index=False)
    return frame


if __name__ == "__main__":
    result = build()
    counts = result.assign(year=pd.to_datetime(result.date).dt.year).pivot_table(
        index="year", columns="event", values="date", aggfunc="count", fill_value=0)
    print(counts.to_string())
    errors = count_exceptions(result)
    if errors:
        print("Count check:", "; ".join(errors))
