"""Paper portfolio viewer: the forward books and the AI picks, marked at live prices. Console look after fidetolabs/qanat
(dark, monospace, thin rules, one lime accent; a stat strip on top, a job log at the bottom).

    scripts/portfolio_ui.sh            (or: cd backend && .venv/bin/streamlit run app/dashboard/portfolio.py)

Nothing here trades. Its one write is the Halt button (kill switch on); the books change only when
forward_allocator.py / forward_events.py are run by hand.
"""
from __future__ import annotations

import html
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `app` importable under streamlit

import altair as alt
import pandas as pd
import streamlit as st

from app.dashboard import portfolio_data as P
from app.portfolio import guard

st.set_page_config(page_title="Paper Book", page_icon="◆", layout="wide")

BG, PANEL, LINE, TEXT, MUTED = "#0a0b0a", "#101210", "#262a25", "#d9dcd4", "#7c8278"
LIME, AMBER, RED = "#a2e65d", "#e8c069", "#ff6b5b"
FONT = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
:root {{ --bg:{BG}; --panel:{PANEL}; --line:{LINE}; --text:{TEXT}; --muted:{MUTED}; --lime:{LIME};
         --amber:{AMBER}; --red:{RED}; }}
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{ background: var(--bg) !important;
  color: var(--text); }}
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded) {{ font-family: {FONT} !important; }}
[data-testid="stHeader"] {{ height: 2.4rem; }}
[data-testid="stSidebar"] {{ background: var(--panel) !important; border-right: 1px solid var(--line); }}
.block-container {{ padding-top: 3rem; max-width: 1400px; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-color: var(--line) !important; border-radius: 4px !important;
  background: var(--panel); }}
.q-head {{ display:flex; align-items:center; gap:.6rem; font-size:.8rem; letter-spacing:.08em; color:var(--muted);
  border-bottom:1px solid var(--line); padding-bottom:.55rem; margin-bottom:.8rem; flex-wrap:wrap; }}
.q-head b {{ color:var(--text); font-weight:700; }}
.q-mark {{ color:var(--lime); }}
.q-pill {{ margin-left:auto; border:1px solid var(--line); padding:.1rem .5rem; border-radius:3px; font-size:.7rem; }}
.q-pill.on {{ color:var(--lime); border-color:var(--lime); }}
.q-pill.halt {{ color:var(--red); border-color:var(--red); }}
.q-strip {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); border:1px solid var(--line);
  border-radius:4px; margin-bottom:.9rem; background:var(--panel); }}
.q-cell {{ padding:.6rem .8rem; border-right:1px solid var(--line); min-width:0; }}
.q-cell:last-child {{ border-right:none; }}
.q-k {{ font-size:.66rem; letter-spacing:.1em; color:var(--muted); text-transform:uppercase; }}
.q-v {{ font-size:1.35rem; color:var(--text); margin-top:.15rem; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }}
.q-s {{ font-size:.72rem; color:var(--muted); margin-top:.1rem; }}
.pos {{ color:var(--lime) !important; }} .neg {{ color:var(--red) !important; }} .warn {{ color:var(--amber) !important; }}
.q-title {{ font-size:.68rem; letter-spacing:.12em; color:var(--muted); text-transform:uppercase; margin:.1rem 0 .5rem; }}
.q-note {{ font-size:.72rem; color:var(--muted); margin:.3rem 0 .2rem; }}
table.q {{ width:100%; border-collapse:collapse; font-size:.78rem; }}
table.q th {{ text-align:left; font-weight:400; color:var(--muted); font-size:.66rem; letter-spacing:.08em;
  text-transform:uppercase; border-bottom:1px solid var(--line); padding:.35rem .4rem; }}
table.q td {{ border-bottom:1px solid #1a1d19; padding:.35rem .4rem; color:var(--text); }}
table.q td.r, table.q th.r {{ text-align:right; }}
.q-bar {{ display:flex; align-items:center; gap:.5rem; font-size:.76rem; margin:.3rem 0; }}
.q-bar .n {{ width:4.6rem; color:var(--text); }} .q-bar .t {{ flex:1; height:8px; background:#1a1d19; border-radius:2px; }}
.q-bar .f {{ height:8px; background:var(--lime); border-radius:2px; }} .q-bar .p {{ width:2.6rem; text-align:right;
  color:var(--muted); }}
.q-log {{ font-size:.74rem; max-height:260px; overflow-y:auto; }}
.q-log div {{ display:grid; grid-template-columns:9.5rem 6.5rem 1fr; gap:.6rem; padding:.18rem 0;
  border-bottom:1px solid #161915; }}
.q-log .t {{ color:var(--muted); }} .q-log .j {{ color:var(--lime); }}
.q-kv {{ display:grid; grid-template-columns:12rem 1fr; gap:.35rem .8rem; font-size:.78rem; }}
.q-kv .k {{ color:var(--muted); font-size:.68rem; letter-spacing:.08em; text-transform:uppercase; padding-top:.1rem; }}
.q-empty {{ border:1px dashed var(--line); color:var(--muted); font-size:.76rem; padding:.9rem; border-radius:4px;
  text-align:center; }}
[data-testid="stSegmentedControl"] button {{ font-size:.72rem !important; letter-spacing:.08em; }}
@media (max-width: 640px) {{ .q-log div {{ grid-template-columns:1fr; gap:0; }} .q-kv {{ grid-template-columns:1fr; }} }}
</style>
""", unsafe_allow_html=True)


# ---------------- small render helpers ----------------

def esc(x: Any) -> str:
    return html.escape(str(x))


def money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.0f}"


def pct(x: float | None, sign: bool = True) -> str:
    return "—" if x is None or pd.isna(x) else (f"{x:+.2%}" if sign else f"{x:.1%}")


def tone(x: float | None) -> str:
    return "" if x is None or pd.isna(x) or x == 0 else ("pos" if x > 0 else "neg")


def strip(cells: list[tuple[str, str, str, str]]) -> None:
    """(key, value, sub, css class of the sub line)."""
    body = "".join(f'<div class="q-cell"><div class="q-k">{esc(k)}</div><div class="q-v">{esc(v)}</div>'
                   f'<div class="q-s {c}">{esc(s)}</div></div>' for k, v, s, c in cells)
    st.markdown(f'<div class="q-strip">{body}</div>', unsafe_allow_html=True)


Col = tuple[str, str, Callable[[Any], str], bool]  # key, header, formatter, right-aligned


def table(rows: list[dict[Any, Any]], cols: list[Col], tones: dict[str, Callable[[Any], str]] | None = None) -> None:
    tones = tones or {}
    head = "".join(f'<th class="{"r" if r else ""}">{esc(h)}</th>' for _, h, _, r in cols)
    body = "".join("<tr>" + "".join(
        f'<td class="{"r " if r else ""}{tones[k](row.get(k)) if k in tones else ""}">{esc(f(row.get(k)))}</td>'
        for k, _, f, r in cols) + "</tr>" for row in rows)
    st.markdown(f'<table class="q"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


def title(t: str) -> None:
    st.markdown(f'<div class="q-title">{esc(t)}</div>', unsafe_allow_html=True)


def empty(t: str) -> None:
    st.markdown(f'<div class="q-empty">{esc(t)}</div>', unsafe_allow_html=True)


def dark(chart: Any) -> Any:
    return (chart.properties(padding={"left": 16, "right": 6, "top": 4, "bottom": 4})
            .configure(background="transparent", font="JetBrains Mono")
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor="#1a1d19", domainColor=LINE,
                            tickColor=LINE, labelFontSize=10, titleFontSize=10, titleFontWeight="normal")
            .configure_view(stroke=None)
            .configure_legend(labelColor=MUTED, titleColor=MUTED, labelFontSize=10, orient="bottom"))


s2 = lambda x: "—" if x is None or pd.isna(x) else str(x)
p2 = lambda x: pct(x)
m0 = lambda x: money(x) if x is not None and not pd.isna(x) else "—"


@st.cache_data(ttl=60, show_spinner=False)
def _prices(tickers: tuple[str, ...], start: date) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    try:
        o, c = P.fetch_prices(list(tickers), start)
        return o, c, None
    except Exception as e:  # noqa: BLE001  network down, Yahoo throttling: show the last marks instead
        return pd.DataFrame(), pd.DataFrame(), f"{type(e).__name__}: {e}"


# ---------------- controls (left rail) ----------------
state, runs = P.load_state(), P.load_runs()
names = [n for n in P.BOOK_LABELS if n in state or any(n in r["books"] for r in runs)] or list(state)
st.sidebar.markdown('<div class="q-head"><span class="q-mark">◆</span><b>PAPER BOOK</b></div>', unsafe_allow_html=True)
book = st.sidebar.selectbox("Book", names, format_func=lambda n: P.BOOK_LABELS.get(n, n)) if names else None
dirs = P.event_dirs()
ev_dir = st.sidebar.selectbox("AI picks ledger", dirs,
                              format_func=lambda n: {"events": "Live S&P 500 (real)",
                                                     "events_breadth": "S&P 400/600 breadth"}.get(n, f"dry run: {n}")
                              ) if dirs else None
live = st.sidebar.toggle("Live prices (60 s)", value=True)
hinfo = guard.halt_info()
tstate = guard.state()
with st.sidebar.expander("Kill switch", expanded=bool(hinfo)):
    if hinfo:
        st.markdown(f'<span class="neg">{tstate}</span> since {esc(hinfo.get("at", "?"))}<br>'
                    f'<span class="q-note">{esc(hinfo.get("reason", ""))}</span><br>'
                    '<span class="q-note">Only <code>forward_allocator.py --resume</code> turns it off.</span>',
                    unsafe_allow_html=True)
    else:
        why = st.text_input("Reason", "stopped from the viewer")
        if st.checkbox("I want to stop all trading") and st.button("Halt trading", type="primary"):
            guard.halt(why, by="portfolio viewer")
            st.rerun()
st.sidebar.markdown('<div class="q-note">Paper money only. Read-only except the Halt button. Yahoo prices can lag a '
                    'few minutes. Not investment advice.</div>', unsafe_allow_html=True)

recs, ledger_err = P.load_events(ev_dir) if ev_dir else ([], None)
picks = P.picks(recs)
open_picks = picks[picks["status"] == "open"]

stage = (f'<span class="q-pill halt">{tstate}: {"NOTHING TRADES" if tstate == "HALTED" else "SELLS ONLY"}</span>'
         if hinfo else
         f'<span class="q-pill {"on" if live else ""}">STAGE: FORWARD · {"LIVE" if live else "PAUSED"}</span>')
st.markdown(f'<div class="q-head"><span class="q-mark">◆</span><b>PAPER BOOK</b> / forward test · paper money · '
            f'{esc(P.BOOK_LABELS.get(book or "", book or "no book"))}{stage}</div>', unsafe_allow_html=True)
surface = st.segmented_control("Surface", ["BOOK", "TRADES", "AI PICKS", "HEALTH"], default="BOOK",
                               label_visibility="collapsed") or "BOOK"


@st.fragment(run_every=timedelta(seconds=60) if live else None)
def console() -> None:
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
        st.markdown(f'<div class="q-note warn">live prices unavailable ({esc(err)}); showing the last run\'s marks'
                    '</div>', unsafe_allow_html=True)

    b = state.get(book or "")
    m = P.mark(b, px) if b and px else None
    last_eq = runs[-1]["books"].get(book, {}).get("equity") if runs and book else None
    eq = (m or {}).get("equity") or last_eq or (b or {}).get("cash") or 0.0
    day_pl = (sum(q * (px[a] - prev[a]) for a, q in b["positions"].items() if a in px and a in prev)
              if b and px else None)
    s = P.scoreboard(picks) if not picks.empty else None
    brake = m["brake"] if m else None
    strip([
        ("equity", money(eq), ("live " + stamp) if m else "last run's mark", ""),
        ("since start", pct(eq / 100_000 - 1) if eq else "—", "from $100,000", tone(eq / 100_000 - 1 if eq else None)),
        ("today", money(day_pl) if day_pl is not None else "—", pct(day_pl / (eq - day_pl)) if day_pl else "no move",
         tone(day_pl)),
        ("drawdown", pct(m["drawdown"], sign=False) if m else "—", "below the peak", "warn" if m and m["drawdown"] > .1
         else ""),
        ("brake", "—" if brake is None else f"{brake:.2f}×", "full exposure" if brake == 1 else
         ("exposure cut" if brake else ""), "" if brake in (None, 1) else "warn"),
        ("ai picks", f"{s['open']} open" if s else "0", f"{s['closed']} closed · {s['missed']} missed" if s else
         "shadow · 0 weight", ""),
    ])

    if surface == "BOOK":
        if not b:
            empty("This book has no saved state yet: it starts at the next forward_allocator.py run.")
        else:
            left, right = st.columns([3, 2], gap="medium")
            with left, st.container(border=True):
                title("equity curve · underwater below")
                hist = P.equity_history(runs)
                if m and m["equity"] and book:
                    hist.loc[pd.Timestamp(datetime.now(UTC).date()), book] = m["equity"]
                series = hist[book].dropna() if book in hist else pd.Series(dtype=float)
                if len(series) >= 2:
                    df = pd.DataFrame({"date": series.index, "equity": series.values,
                                       "under": P.underwater(series).values})
                    top = alt.Chart(df).mark_line(color=LIME, strokeWidth=1.6).encode(
                        x=alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False)),
                        y=alt.Y("equity:Q", scale=alt.Scale(zero=False), title=None, axis=alt.Axis(format="$,.0f")),
                        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("equity:Q", format="$,.0f")]).properties(height=210)
                    bot = alt.Chart(df).mark_area(color=MUTED, opacity=.45, line={"color": MUTED}).encode(
                        x=alt.X("date:T", title=None), y=alt.Y("under:Q", title=None, axis=alt.Axis(format=".0%", tickCount=3)),
                        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("under:Q", format=".1%")]).properties(height=80)
                    st.altair_chart(dark(alt.vconcat(top, bot, spacing=4)), use_container_width=True, theme=None)
                else:
                    empty("The curve starts after the second allocator run. The forward test starts Mon 5 Oct.")
            with right, st.container(border=True):
                title("allocation")
                alloc = {p["asset"]: p["value"] for p in (m["positions"] if m else [])}
                pending_only = not b["positions"] and b.get("pending")
                if pending_only:
                    alloc = {a: w * eq for a, w in b["pending"].items()}
                else:
                    alloc["cash"] = b["cash"]
                tot = sum(alloc.values()) or 1.0
                bars = "".join(f'<div class="q-bar"><span class="n">{esc(a)}</span><span class="t"><span class="f" '
                               f'style="display:block;width:{100 * v / tot:.1f}%"></span></span>'
                               f'<span class="p">{v / tot:.0%}</span></div>' for a, v in
                               sorted(alloc.items(), key=lambda kv: -kv[1]))
                st.markdown(bars + ('<div class="q-note">target weights, not filled yet</div>' if pending_only
                                    else ""), unsafe_allow_html=True)
            c1, c2 = st.columns([3, 2], gap="medium")
            with c1, st.container(border=True):
                title("holdings")
                if b["positions"]:
                    rows = m["positions"] if m else [{"asset": a, "qty": q} for a, q in b["positions"].items()]
                    for row in rows:
                        row["today"] = (px[row["asset"]] / prev[row["asset"]] - 1) if row["asset"] in prev else None
                    table(rows, [("asset", "asset", s2, False), ("qty", "qty", lambda x: f"{x:,.4f}", True),
                                 ("price", "price", lambda x: "—" if x is None else f"{x:,.2f}", True),
                                 ("value", "value", m0, True), ("weight", "weight", lambda x: pct(x, False), True),
                                 ("today", "today", p2, True)], {"today": tone})
                else:
                    empty("No holdings yet: everything is in cash.")
            with c2, st.container(border=True):
                title("orders waiting")
                if b.get("pending"):
                    table([{"asset": a, "w": w, "usd": w * eq} for a, w in b["pending"].items()],
                          [("asset", "asset", s2, False), ("w", "target", lambda x: pct(x, False), True),
                           ("usd", "≈ $", m0, True)])
                    st.markdown(f'<div class="q-note">decided {esc(b.get("decided_at", "?"))} UTC · fills at the first '
                                'open after that date, at the next allocator run · checked against the mandate '
                                'again before filling</div>', unsafe_allow_html=True)
                else:
                    empty("Nothing waiting.")

    elif surface == "TRADES":
        with st.container(border=True):
            title("fills · newest first")
            t = P.trades(runs)
            if book and st.toggle("This book only", value=True):
                t = t[t["book"] == book]
            if t.empty:
                empty("No fills yet. The first orders fill at the next allocator run after they were decided.")
            else:
                table(t.to_dict("records"), [("filled_on", "filled", s2, False), ("book", "book", s2, False),
                                             ("side", "side", s2, False), ("asset", "asset", s2, False),
                                             ("qty", "qty", lambda x: f"{x:,.4f}", True),
                                             ("price", "price", lambda x: f"{x:,.2f}", True),
                                             ("value", "value", m0, True)],
                      {"side": lambda x: "pos" if x == "buy" else "neg"})

    elif surface == "AI PICKS":
        st.markdown('<div class="q-note">1-week Bonsai book on new S&P 500 earnings releases · shadow book at 0 weight '
                    '(scored, never traded) · score = 1-week log-odds, higher is more bullish · returns vs the '
                    'sector ETF</div>', unsafe_allow_html=True)
        if ledger_err:
            st.markdown(f'<div class="q-note neg">ledger check failed: {esc(ledger_err)}</div>', unsafe_allow_html=True)
        if picks.empty or s is None:
            empty("No picks in this ledger yet. The real one starts with the first forward_events.py run.")
        else:
            ic = s["bonsai_ic"] if s["bonsai_ic"] is not None else s["lite_ic"]
            done = picks.dropna(subset=["fwd5"])
            strip([("decisions", str(s["decisions"]), "on time", ""), ("open", str(s["open"]), "holding 5 days", ""),
                   ("closed", str(s["closed"]), "matured", ""), ("missed", str(s["missed"]), "never backfilled",
                                                               "warn" if s["missed"] else ""),
                   ("rank ic", "—" if ic is None else f"{ic:+.3f}", "backtest +0.10 · needs 10", tone(ic)),
                   ("avg 5-day", pct(float(done["fwd5"].mean())) if len(done) else "—", "vs sector",
                    tone(float(done["fwd5"].mean())) if len(done) else "")])
            if not open_picks.empty:
                with st.container(border=True):
                    title("open picks · live vs sector")
                    op = open_picks.copy()
                    live_x = P.live_excess(op, opens, closes) if not closes.empty else pd.Series(dtype=float)
                    op["so_far"] = op["accession"].map(live_x)
                    table(op.sort_values("score", ascending=False).to_dict("records"),
                          [("ticker", "ticker", s2, False), ("sector", "sector", s2, False),
                           ("entry", "entry", s2, False), ("source", "source", s2, False),
                           ("score", "score", lambda x: f"{x:+.2f}", True), ("so_far", "so far", p2, True)],
                          {"so_far": tone})
            if len(done):
                c1, c2 = st.columns([3, 2], gap="medium")
                with c1, st.container(border=True):
                    title("closed picks · 5-day vs sector")
                    table(done.sort_values("entry", ascending=False).to_dict("records"),
                          [("ticker", "ticker", s2, False), ("entry", "entry", s2, False),
                           ("source", "source", s2, False), ("score", "score", lambda x: f"{x:+.2f}", True),
                           ("fwd5", "5-day", p2, True)], {"fwd5": tone})
                with c2, st.container(border=True):
                    title("score vs result")
                    ch = alt.Chart(done).mark_circle(size=60, color=LIME, opacity=.85).encode(
                        x=alt.X("score:Q", title="score", scale=alt.Scale(zero=False)),
                        y=alt.Y("fwd5:Q", title="5-day vs sector", axis=alt.Axis(format="%")),
                        tooltip=["ticker", "entry", alt.Tooltip("score:Q", format="+.2f"),
                                 alt.Tooltip("fwd5:Q", format="+.2%")]).properties(height=230)
                    rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=LINE).encode(y="y:Q")
                    st.altair_chart(dark(rule + ch), use_container_width=True, theme=None)
            miss = picks[picks["status"] == "missed"]
            if not miss.empty:
                with st.container(border=True):
                    title(f"missed releases ({len(miss)})")
                    table(miss.to_dict("records"), [("ticker", "ticker", s2, False), ("entry", "entry", s2, False),
                                                    ("reason", "reason", s2, False)])

    else:  # HEALTH
        lt = P.last_run_times(runs, recs)
        gaps = P.missed_weekdays(recs)
        try:
            md = guard.load_mandate()
            mandate = (f"{', '.join(sorted(md.universe))} · ≤{md.max_weight:.0%} one asset · ≤{md.max_gross:.0%} "
                       f"invested · ≤{md.max_crypto:.0%} crypto · checked when decided and again before filling · alert at "
                       f"{md.alert_drawdown:.0%} below the peak, sells only (REDUCING) at {md.max_drawdown:.0%}")
            mcls = ""
        except guard.MandateError as e:
            mandate, mcls = f"UNREADABLE, so every order is rejected: {e}", "neg"
        kv = [("last allocator run", (lt["allocator"] or "never")[:16].replace("T", " ") + " UTC", ""),
              ("last event run", (lt["events"] or "never")[:16].replace("T", " ") + " UTC", ""),
              ("event ledger", ledger_err or f"hash chain verified · {len(recs)} records", "neg" if ledger_err
               else "pos"),
              ("weekdays with no run", f"{len(gaps)}" + (f" ({', '.join(d.isoformat() for d in gaps[-6:])})"
                                                         if gaps else ""), "warn" if gaps else ""),
              ("mandate", mandate, mcls),
              ("trading state", f"{tstate} · {hinfo.get('reason', '')}" if hinfo else "ACTIVE", "neg" if hinfo
               else "pos"),
              ("routine (by hand)", ("forward_events.py weekdays ~08:45 ET and evening · forward_allocator.py "
                                     "weekly · weekly_review.py Saturdays"), "")]
        with st.container(border=True):
            title("inspector")
            st.markdown('<div class="q-kv">' + "".join(f'<div class="k">{esc(k)}</div><div class="{c}">{esc(v)}</div>'
                                                       for k, v, c in kv) + "</div>", unsafe_allow_html=True)

    with st.container(border=True):
        title("run log · newest first · every line was written by a job that ran")
        log = P.run_log(runs, recs, hinfo)
        if log:
            st.markdown('<div class="q-log">' + "".join(
                f'<div><span class="t">{esc(x["at"][:16].replace("T", " "))}</span><span class="j">{esc(x["job"])}'
                f'</span><span class="{"neg" if any(w in x["what"] for w in ("REJECTED", "MISSED", "LIMIT")) else ""}">'
                f'{esc(x["what"])}</span></div>' for x in log) + "</div>", unsafe_allow_html=True)
        else:
            empty("Nothing has run yet.")


console()
