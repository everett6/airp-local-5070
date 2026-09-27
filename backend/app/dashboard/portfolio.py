"""Paper portfolio viewer: the forward books and the AI picks, marked at live prices. Read-only.

    cd backend && .venv/bin/streamlit run app/dashboard/portfolio.py --server.port 8502

Nothing here trades or writes: the books change only when forward_allocator.py / forward_events.py are run by hand.
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `app` importable under streamlit

import altair as alt
import pandas as pd
import streamlit as st

from app.dashboard import portfolio_data as P

st.set_page_config(page_title="Paper Portfolio", page_icon="📈", layout="wide")


@st.cache_data(ttl=60, show_spinner=False)
def _prices(tickers: tuple[str, ...], start: date) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    try:
        o, c = P.fetch_prices(list(tickers), start)
        return o, c, None
    except Exception as e:  # noqa: BLE001  network down, Yahoo throttling: show the last marks instead
        return pd.DataFrame(), pd.DataFrame(), f"{type(e).__name__}: {e}"


def money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.0f}"


def pct(x: float | None, sign: bool = True) -> str:
    return "—" if x is None or pd.isna(x) else (f"{x:+.2%}" if sign else f"{x:.1%}")


# ---------------- sidebar ----------------
st.sidebar.title("📈 Paper Portfolio")
state, runs = P.load_state(), P.load_runs()
names = [n for n in P.BOOK_LABELS if n in state or any(n in r["books"] for r in runs)] or list(state)
book = st.sidebar.selectbox("Book", names, format_func=lambda n: P.BOOK_LABELS.get(n, n)) if names else None
dirs = P.event_dirs()
ev_dir = st.sidebar.selectbox("AI picks ledger", dirs,
                              format_func=lambda n: {"events": "Live S&P 500 (real)",
                                                     "events_breadth": "S&P 400/600 breadth"}.get(n, f"dry run: {n}")
                              ) if dirs else None
live = st.sidebar.toggle("Live prices", value=True, help="Marks the books at Yahoo's latest prices every minute.")
st.sidebar.caption("Paper money only. Read-only: the books change only when the forward runners are run by hand. "
                   "Yahoo prices can lag a few minutes. Not investment advice.")

recs, ledger_err = P.load_events(ev_dir) if ev_dir else ([], None)
picks = P.picks(recs)
open_picks = picks[picks["status"] == "open"]


@st.fragment(run_every=timedelta(seconds=60) if live else None)
def live_view() -> None:
    first = date.fromisoformat(runs[0]["data_through"]) if runs else datetime.now(UTC).date()
    ents = pd.to_datetime(open_picks["entry"], errors="coerce").dropna()
    start = min([first, *(d.date() for d in ents)]) - timedelta(days=10)
    tickers = set(P.book_assets(state)) | {"SPY"}
    for r in open_picks.itertuples():
        tickers |= {str(r.ticker).replace(".", "-"), P.SECTOR_ETF.get(str(r.sector), "SPY")}
    opens, closes, err = _prices(tuple(sorted(tickers)), start) if live else (pd.DataFrame(), pd.DataFrame(), None)
    px = P.latest(closes) if not closes.empty else {}
    prev = {c: float(closes[c].dropna().iloc[-2]) for c in closes.columns if closes[c].notna().sum() >= 2}
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    if err:
        st.warning(f"Live prices unavailable ({err}). Showing the last marks from the ledger.")

    tab_pf, tab_tr, tab_ai, tab_health = st.tabs(["Portfolio", "Trades", "AI picks", "Run health"])

    # ---------- portfolio ----------
    with tab_pf:
        if not book or book not in state:
            st.info("This book has no saved state yet: it starts at the next `forward_allocator.py` run.")
        else:
            b = state[book]
            last_eq = runs[-1]["books"].get(book, {}).get("equity") if runs else None
            m = P.mark(b, px) if px else None
            eq = (m or {}).get("equity") or last_eq or b["cash"]
            start_eq = 100_000.0
            day_pl = sum(q * (px[a] - prev[a]) for a, q in b["positions"].items() if a in px and a in prev) if px else None
            c1, c2, c3 = st.columns(3)
            c4, c5, c6 = st.columns(3)
            c1.metric("Equity" + (" (live)" if m and m["equity"] else ""), money(eq), pct(eq / start_eq - 1))
            c2.metric("Today", money(day_pl) if day_pl is not None else "—",
                      pct(day_pl / (eq - day_pl)) if day_pl else None)
            c3.metric("Cash", money(b["cash"]), pct(b["cash"] / eq, sign=False), delta_color="off", delta_arrow="off")
            c4.metric("Drawdown", pct(m["drawdown"], sign=False) if m else "—", delta_color="off", delta_arrow="off")
            brake = m["brake"] if m else None
            c5.metric("Brake level", "—" if brake is None else f"{brake:.2f}×",
                      None if brake is None else ("full" if brake == 1 else "cut"), delta_color="off", delta_arrow="off")
            c6.metric("Trades / costs", f"{b.get('trades', 0)}", money(b.get("costs", 0.0)), delta_color="off", delta_arrow="off")
            st.caption(f"Updated {stamp}" + (" · live marks, not trades" if px else " · last run's mark"))

            left, right = st.columns([3, 2])
            with left:
                st.subheader("Holdings")
                if b["positions"]:
                    rows = pd.DataFrame(m["positions"] if m else
                                        [{"asset": a, "qty": q} for a, q in b["positions"].items()])
                    if "price" in rows:
                        rows["today"] = [(px[a] / prev[a] - 1) if a in prev else None for a in rows["asset"]]
                    st.dataframe(rows, hide_index=True, use_container_width=True, column_config={
                        "qty": st.column_config.NumberColumn("Quantity", format="%.4f"),
                        "price": st.column_config.NumberColumn("Price", format="$%.2f"),
                        "value": st.column_config.NumberColumn("Value", format="$%.0f"),
                        "weight": st.column_config.NumberColumn("Weight", format="percent"),
                        "today": st.column_config.NumberColumn("Today", format="percent")})
                else:
                    st.info("No holdings yet: everything is in cash.")
                if b.get("pending"):
                    st.subheader("Orders waiting")
                    when = str(b.get("decided_at", ""))[:10]
                    st.caption(f"Decided {b.get('decided_at', '?')} UTC. They fill at the open of the first "
                               f"trading day after {when}, at the next `forward_allocator.py` run.")
                    st.dataframe(pd.DataFrame([{"asset": a, "target weight": w, "target $": w * eq}
                                               for a, w in b["pending"].items()]), hide_index=True,
                                 use_container_width=True, column_config={
                                     "target weight": st.column_config.NumberColumn(format="percent"),
                                     "target $": st.column_config.NumberColumn(format="$%.0f")})
            with right:
                st.subheader("Allocation")
                alloc = {p["asset"]: p["value"] for p in (m["positions"] if m else [])} | {"Cash": b["cash"]}
                if not b["positions"] and b.get("pending"):
                    alloc = {a: w * eq for a, w in b["pending"].items()}
                    st.caption("Target allocation (not filled yet)")
                df = pd.DataFrame({"asset": list(alloc), "value": list(alloc.values())})
                st.altair_chart(alt.Chart(df).mark_arc(innerRadius=60).encode(
                    theta="value:Q", color=alt.Color("asset:N", legend=alt.Legend(orient="bottom")),
                    tooltip=["asset", alt.Tooltip("value:Q", format="$,.0f")]), use_container_width=True)

            st.subheader("Equity over time")
            hist = P.equity_history(runs)
            if m and m["equity"]:
                hist.loc[pd.Timestamp(datetime.now(UTC).date()), book] = m["equity"]
            if len(hist) >= 2:
                long = hist.reset_index(names="date").melt("date", var_name="book", value_name="equity").dropna()
                long["book"] = long["book"].map(lambda n: P.BOOK_LABELS.get(n, n))
                st.altair_chart(alt.Chart(long).mark_line(point=True).encode(
                    x="date:T", y=alt.Y("equity:Q", scale=alt.Scale(zero=False), title="Equity ($)"),
                    color="book:N", tooltip=["date:T", "book", alt.Tooltip("equity:Q", format="$,.0f")]),
                    use_container_width=True)
            else:
                st.info("The curve appears after the second allocator run (the forward test starts Mon 5 Oct).")

    # ---------- trades ----------
    with tab_tr:
        t = P.trades(runs)
        if book:
            t = t[t["book"] == book] if st.toggle("This book only", value=True) else t
        if t.empty:
            st.info("No fills yet. The first orders fill at the next allocator run after they were decided.")
        else:
            st.dataframe(t, hide_index=True, use_container_width=True, column_config={
                "price": st.column_config.NumberColumn(format="$%.2f"),
                "value": st.column_config.NumberColumn(format="$%.0f"),
                "qty": st.column_config.NumberColumn(format="%.4f")})

    # ---------- AI picks ----------
    with tab_ai:
        st.caption("The 1-week Bonsai book on new earnings releases. **Shadow book at 0 weight:** it is scored, "
                   "never traded. Score = Bonsai's 1-week log-odds (higher = more bullish). Returns are vs the "
                   "stock's sector ETF.")
        if ledger_err:
            st.error(f"Ledger check failed: {ledger_err}")
        if picks.empty:
            st.info("No picks in this ledger yet. The real one starts with the first `forward_events.py` run.")
        else:
            s = P.scoreboard(picks)
            c1, c2, c3 = st.columns(3)
            c4, c5, c6 = st.columns(3)
            c1.metric("Decisions", s["decisions"])
            c2.metric("Open", s["open"])
            c3.metric("Closed", s["closed"])
            c4.metric("Missed", s["missed"])
            ic = s["bonsai_ic"] if s["bonsai_ic"] is not None else s["lite_ic"]
            c5.metric("Rank IC (closed)", "—" if ic is None else f"{ic:+.3f}",
                      help="Needs 10 closed picks per source. Backtest: +0.10 (2025-26), +0.03 (2024).")
            done_x = picks.dropna(subset=["fwd5"])
            c6.metric("Avg 5-day vs sector", pct(float(done_x["fwd5"].mean())) if len(done_x) else "—")
            if not open_picks.empty:
                st.subheader("Open picks (live, vs sector)")
                op = open_picks.copy()
                op["so far"] = op["accession"].map(P.live_excess(op, opens, closes)) if not closes.empty else None
                st.dataframe(op[["ticker", "sector", "entry", "source", "score", "so far"]].sort_values(
                    "score", ascending=False), hide_index=True, use_container_width=True,
                    column_config={"so far": st.column_config.NumberColumn(format="percent")})
            done = picks[picks["status"] == "closed"]
            if not done.empty:
                st.subheader("Closed picks (5-day return vs sector)")
                st.dataframe(done[["ticker", "sector", "entry", "source", "score", "fwd5"]].sort_values(
                    "entry", ascending=False), hide_index=True, use_container_width=True,
                    column_config={"fwd5": st.column_config.NumberColumn("5-day vs sector", format="percent")})
                st.altair_chart(alt.Chart(done.dropna(subset=["fwd5"])).mark_circle(size=70).encode(
                    x=alt.X("score:Q", title="Score"), y=alt.Y("fwd5:Q", title="5-day vs sector", axis=alt.Axis(
                        format="%")), color="source:N", tooltip=["ticker", "entry", "score",
                                                                 alt.Tooltip("fwd5:Q", format="+.2%")]),
                    use_container_width=True)
            miss = picks[picks["status"] == "missed"]
            if not miss.empty:
                with st.expander(f"Missed releases ({len(miss)})"):
                    st.dataframe(miss[["ticker", "entry", "reason"]], hide_index=True, use_container_width=True)

    # ---------- health ----------
    with tab_health:
        lt = P.last_run_times(runs, recs)
        c1, c2 = st.columns(2)
        c1.metric("Last allocator run (UTC)", (lt["allocator"] or "never")[:16].replace("T", " "))
        c2.metric("Last event run (UTC)", (lt["events"] or "never")[:16].replace("T", " "))
        gaps = P.missed_weekdays(recs)
        st.write(f"**Event ledger:** {'⚠️ ' + ledger_err if ledger_err else '✅ hash chain verified'} · "
                 f"{len(recs)} records · weekdays with no run: {len(gaps)}"
                 + (f" ({', '.join(d.isoformat() for d in gaps[-10:])})" if gaps else ""))
        st.markdown("**Routine (all by hand):**\n"
                    "- `forward_events.py` every weekday at ~08:45 ET **and** in the evening\n"
                    "- `forward_allocator.py` about weekly\n"
                    "- `weekly_review.py` every Saturday")


live_view()
