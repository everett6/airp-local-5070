"""AIRP Walk-Forward Lab — Streamlit dashboard.

    cd backend && .venv/bin/streamlit run app/dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # make `app` importable under streamlit

import altair as alt
import pandas as pd
import streamlit as st

from app.dashboard import data as D

st.set_page_config(page_title="AIRP Walk-Forward Lab", page_icon="🧪", layout="wide")

COLORS = {
    "Always 'up'": "#9aa0a6", "Base rate": "#bdc1c6", "Momentum (20d)": "#80868b",
    "Reversal (5d)": "#5f6368", "Logistic (no LLM)": "#f9ab00", "LLM": "#1a73e8",
    "LLM + self-improve": "#d93025", "Selector": "#188038",
}
COLOR_SCALE = alt.Scale(domain=list(COLORS), range=list(COLORS.values()))


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


# ---------------- sidebar ----------------
runs = D.list_runs()
st.sidebar.title("🧪 Walk-Forward Lab")
if not runs:
    st.sidebar.warning("No results yet. Start one in the **New run** tab.")
run_labels = {f"{r['tag']} · {r['model']} · {r['target']}": r["tag"] for r in runs}
tag = run_labels[st.sidebar.selectbox("Run", list(run_labels))] if runs else None
after_warmup = st.sidebar.toggle("Score after warm-up only", value=True,
                                 help="Learning arms need resolved history first. The write-up uses after-warm-up scores.")
st.sidebar.caption("Every prediction was made by an agent in a process jail with no network or file "
                   "access, on anonymized prices, using only outcomes resolved before its cutoff.")

tabs = st.tabs(["Overview", "Over time", "Calibration", "Self-improvement",
                "Predictions", "Compare runs", "New run", "Live research"])

if tag:
    report = D.load_report(tag)
    preds_all = D.load_predictions(tag)
    preds = D.warm_filter(preds_all, report, after_warmup)
    scores = D.scores_frame(report, after_warmup)
    saved_arms = D._ordered(sorted(preds["arm"].unique())) if not preds.empty else []
    arms_sel = st.sidebar.multiselect("Arms in charts", saved_arms, default=saved_arms,
                                      format_func=lambda a: D.ARM_LABELS.get(a, a))
    preds_sel = preds[preds["arm"].isin(arms_sel)]

    # ---------------- overview ----------------
    with tabs[0]:
        st.header(f"Run `{tag}`")
        st.caption(f"{report['model']} · target **{report.get('target', 'abs')}** "
                   f"({'up vs down' if report.get('target', 'abs') == 'abs' else 'beat SPY or not'}) · "
                   f"{report['window'][0]} → {report['window'][1]} · {report['horizon_days']}-day horizon · "
                   f"{len(report['tickers'])} stocks")
        n = int(scores["n"].iloc[0])
        best = scores.loc[scores["brier"].idxmin()]
        jail_ok = report["jail_probe_start"]["passed"] and report["jail_probe_end"]["passed"]
        c = st.columns(4)
        c[0].metric("Predictions scored", f"{n:,}", help=f"{report['n_cutoffs']} cutoffs; "
                    f"{'from ' + report['warmup_from'] if after_warmup else 'full window'}")
        c[1].metric("Best arm (Brier)", str(best["label"]), f"{best['brier']:.4f}", delta_color="off")
        c[2].metric("Coin-flip Brier", "0.2500", help="Lower is better. 0.25 = always saying 50%.")
        c[3].metric("Agent isolation", "✅ Jailed" if jail_ok else "❌ FAILED",
                    help="Live probe at start and end: no readable data files, no network.")

        gaps = {a: D.brier_gap_ci(preds, a) for a in ("llm_plain", "llm_selfimprove") if a in saved_arms}
        gaps = {a: g for a, g in gaps.items() if g}
        if "always_up" in saved_arms and gaps:
            winners = [a for a, g in gaps.items() if g["hi"] < 0]
            if winners:
                st.success("Beat 'always up' with 95% confidence: "
                           + ", ".join(D.ARM_LABELS[a] for a in winners))
            else:
                naive = "'up'" if report.get("target", "abs") == "abs" else "'beats the market'"
                st.warning(f"**No LLM arm beat simply always predicting {naive}** — every Brier gap below is "
                           "within noise or worse.")
        elif not gaps:
            st.info("This run's prediction file only has some arms; re-run it to get the full comparison.")

        left, right = st.columns([3, 2])
        with left:
            st.subheader("Scores")
            se = 0.5 / n ** 0.5
            show = scores.rename(columns={"label": "Arm", "accuracy": "Accuracy", "pct_up_calls": "% up calls",
                                          "brier": "Brier", "log_loss": "Log loss",
                                          "ls_mean_weekly_ret_pct": "L/S weekly %", "ls_sharpe_ann": "L/S Sharpe"})
            show = show[["Arm", "Accuracy", "% up calls", "Brier", "Log loss", "L/S weekly %", "L/S Sharpe"]]
            st.dataframe(
                show.style.format({"Accuracy": pct, "% up calls": pct, "Brier": "{:.4f}", "Log loss": "{:.4f}",
                                   "L/S weekly %": "{:+.2f}", "L/S Sharpe": "{:.2f}"})
                .highlight_min(subset=["Brier", "Log loss"], color="#d2e3fc"),
                hide_index=True, width="stretch")
            st.caption(f"1 standard error of accuracy ≈ ±{se * 100:.1f} pts (assumes independent predictions; "
                       "the real uncertainty is larger, see the Brier gap table). L/S figures ignore costs.")
        with right:
            st.subheader("Accuracy vs a coin flip")
            acc = scores.assign(edge=(scores["accuracy"] - 0.5) * 100, zero=0.0,
                                lo=(scores["accuracy"] - 0.5 - se) * 100, hi=(scores["accuracy"] - 0.5 + se) * 100)
            lim = max(8.0, float(acc[["lo", "hi"]].abs().max().max()) + 1)
            base = alt.Chart(acc).encode(y=alt.Y("label:N", sort=None, title=None))
            chart = alt.layer(
                base.mark_bar().encode(
                    x=alt.X("edge:Q", scale=alt.Scale(domain=[-lim, lim]), title="points above/below 50%"),
                    x2="zero:Q", color=alt.Color("label:N", scale=COLOR_SCALE, legend=None),
                    tooltip=["label", alt.Tooltip("accuracy:Q", format=".1%")]),
                base.mark_rule(color="black").encode(x="lo:Q", x2="hi:Q"),
            ).properties(height=260)
            st.altair_chart(chart, width="stretch")
            st.caption("Black lines = ±1 standard error.")

        with st.expander("🧾 Receipts: exactly what produced this run"):
            rows_pv = D.provenance_rows(report)
            if rows_pv is None:
                st.info("This run predates provenance tracking. Re-run it with `--config configs/<tag>.toml` "
                        "to stamp it, or check it with `python scripts/reproduce.py`.")
            else:
                st.dataframe(pd.DataFrame(rows_pv), hide_index=True, width="stretch")
                if report.get("config"):
                    st.json(report["config"], expanded=False)

        if gaps or "always_up" in saved_arms:
            st.subheader("Is any arm really better than 'always up'?")
            rows = []
            for a in saved_arms:
                if a == "always_up":
                    continue
                g = D.brier_gap_ci(preds, a)
                if g:
                    verdict = "better" if g["hi"] < 0 else ("worse" if g["lo"] > 0 else "no clear difference")
                    rows.append({"Arm": D.ARM_LABELS.get(a, a), "Brier gap": g["gap"], "95% CI low": g["lo"],
                                 "95% CI high": g["hi"], "Verdict": verdict})
            if rows:
                st.dataframe(pd.DataFrame(rows).style.format(
                    {"Brier gap": "{:+.4f}", "95% CI low": "{:+.4f}", "95% CI high": "{:+.4f}"}),
                    hide_index=True, width="stretch")
                st.caption("Gap = arm's Brier minus always-up's on the same predictions (negative = arm better). "
                           "The interval comes from resampling whole weeks, because the 20 stocks in a week "
                           "move together.")

    # ---------------- over time ----------------
    with tabs[1]:
        st.header("Over time")
        ot = D.over_time(preds_sel)
        if ot.empty:
            st.info("No predictions saved for this run.")
        else:
            metric = st.radio("Show", ["Cumulative accuracy", "Long/short growth (no costs)", "Accuracy per cutoff"],
                              horizontal=True)
            col, fmt, rule = {"Cumulative accuracy": ("cum_accuracy", "%", 0.5),
                              "Long/short growth (no costs)": ("cum_ls_growth", "%", 0.0),
                              "Accuracy per cutoff": ("accuracy", "%", 0.5)}[metric]
            line = alt.Chart(ot).mark_line(point=metric == "Accuracy per cutoff").encode(
                x=alt.X("cutoff:T", title="cutoff"), y=alt.Y(f"{col}:Q", axis=alt.Axis(format=fmt), title=metric, scale=alt.Scale(zero=False)),
                color=alt.Color("label:N", scale=COLOR_SCALE, title="arm"),
                tooltip=["label", alt.Tooltip("cutoff:T"), alt.Tooltip(f"{col}:Q", format=".1%")])
            ref = alt.Chart(pd.DataFrame({"y": [rule]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q")
            st.altair_chart((line + ref).interactive(bind_y=False), width="stretch")
            st.caption("Early cumulative values swing a lot because they're based on few predictions.")

    # ---------------- calibration ----------------
    with tabs[2]:
        st.header("Calibration")
        st.caption("When an arm says 60%, does the stock go up 60% of the time? Points on the dashed line "
                   "are perfectly calibrated. Rule baselines only ever say 49% or 51%. Bins with under 15 predictions are hidden.")
        cal = D.calibration(preds_sel)
        cal = cal[cal["n"] >= 15] if not cal.empty else cal
        if cal.empty:
            st.info("No predictions saved for this run.")
        else:
            diag = alt.Chart(pd.DataFrame({"x": [0.3, 0.75], "y": [0.3, 0.75]})).mark_line(strokeDash=[4, 4], color="gray") \
                .encode(x="x:Q", y="y:Q")
            enc = {
                "x": alt.X("mean_p:Q", scale=alt.Scale(domain=[0.3, 0.75], clamp=True), title="forecast probability of up"),
                "y": alt.Y("realized:Q", scale=alt.Scale(domain=[0.25, 0.8], clamp=True), title="actual up rate"),
                "color": alt.Color("label:N", scale=COLOR_SCALE, title="arm"),
            }
            lines = alt.Chart(cal).mark_line(opacity=0.6).encode(**enc)
            pts = alt.Chart(cal).mark_circle(opacity=0.9).encode(
                **enc, size=alt.Size("n:Q", title="predictions"),
                tooltip=["label", alt.Tooltip("mean_p:Q", format=".2f"), alt.Tooltip("realized:Q", format=".2f"), "n"])
            st.altair_chart(alt.layer(diag, lines, pts).properties(height=420), width="stretch")

    # ---------------- self-improvement ----------------
    with tabs[3]:
        st.header("Self-improvement")
        st.markdown("The self-improving agent sees its own resolved track record, writes itself lessons, and may "
                    "switch on a small learned correction (the *stacker*). That correction is **only adopted if "
                    "it beats the raw LLM on a held-out slice of past outcomes.**")
        a, b = st.columns(2)
        with a:
            st.subheader("Stacker decisions")
            sl = pd.DataFrame(report.get("stacker_log", []))
            if sl.empty:
                st.info("No stacker log in this run.")
            else:
                sl["cutoff"] = pd.to_datetime(sl["cutoff"])
                st.altair_chart(alt.Chart(sl).mark_tick(thickness=4, size=30).encode(
                    x="cutoff:T", y=alt.Y("stacker:N", title=None),
                    color=alt.Color("stacker:N", legend=None,
                                    scale=alt.Scale(domain=["adopted", "rejected", "not_enough_data"],
                                                    range=["#188038", "#d93025", "#9aa0a6"]))),
                    width="stretch")
                st.caption(" · ".join(f"{k}: {v}" for k, v in sl["stacker"].value_counts().items()))
        with b:
            st.subheader("Selector's choice each cutoff")
            sel = pd.DataFrame(report.get("selector_log", []))
            if sel.empty:
                st.info("No selector log in this run.")
            else:
                sel["cutoff"] = pd.to_datetime(sel["cutoff"])
                sel["label"] = sel["chosen"].map(lambda x: D.ARM_LABELS.get(x, x))
                st.altair_chart(alt.Chart(sel).mark_tick(thickness=4, size=30).encode(
                    x="cutoff:T", y=alt.Y("label:N", title=None),
                    color=alt.Color("label:N", scale=COLOR_SCALE, legend=None)), width="stretch")
        st.subheader("Lessons the agent wrote for itself")
        lessons = report.get("lessons", [])
        if not lessons:
            st.info("No reflection lessons in this run.")
        for entry in reversed(lessons):
            with st.expander(f"Lessons as of {entry['cutoff']}"):
                for lesson in entry["lessons"]:
                    st.markdown(f"- {lesson}")

    # ---------------- predictions ----------------
    with tabs[4]:
        st.header("Predictions")
        if preds.empty:
            st.info("No predictions saved for this run.")
        else:
            f1, f2, f3 = st.columns(3)
            arm_p = f1.selectbox("Arm", saved_arms, index=saved_arms.index("llm_plain") if "llm_plain" in saved_arms
                                 else 0, format_func=lambda a: D.ARM_LABELS.get(a, a))
            tick = f2.multiselect("Stocks", sorted(preds["ticker"].unique()))
            outcome = f3.radio("Show", ["All", "Correct", "Wrong"], horizontal=True)
            view = preds[preds["arm"] == arm_p]
            if tick:
                view = view[view["ticker"].isin(tick)]
            hit = (view["p"] >= 0.5) == view["up"]
            view = view[hit] if outcome == "Correct" else view[~hit] if outcome == "Wrong" else view
            out = view.assign(call=view["p"].map(lambda p: "up" if p >= 0.5 else "down"),
                              actual=view["up"].map(lambda u: "up" if u else "down"),
                              correct=((view["p"] >= 0.5) == view["up"]))
            out = out[["cutoff", "ticker", "p", "call", "actual", "ret", "correct", "resolve_date"]]
            st.dataframe(out.sort_values(["cutoff", "ticker"], ascending=[False, True]).style.format(
                {"p": "{:.2f}", "ret": lambda r: f"{r * 100:+.2f}%", "cutoff": "{:%Y-%m-%d}",
                 "resolve_date": "{:%Y-%m-%d}"}), hide_index=True, width="stretch", height=380)
            st.download_button("Download CSV", out.to_csv(index=False), f"{tag}_{arm_p}.csv", "text/csv")
            st.subheader("Accuracy by stock")
            by = preds[preds["arm"] == arm_p].assign(hit=lambda d: (d["p"] >= 0.5) == d["up"]) \
                .groupby("ticker")["hit"].mean().reset_index()
            st.altair_chart(alt.Chart(by).mark_bar().encode(
                x=alt.X("ticker:N", sort="-y"), y=alt.Y("hit:Q", axis=alt.Axis(format="%"), title="accuracy"),
                tooltip=["ticker", alt.Tooltip("hit:Q", format=".1%")])
                + alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q"),
                width="stretch")
            st.caption("The agent never saw these ticker names; this breakdown is added afterwards for you.")

# ---------------- compare runs ----------------
with tabs[5]:
    st.header("Compare runs")
    if runs:
        cmp = D.compare_runs(after_warmup=after_warmup)
        fmts = {c: (pct if c.endswith("acc") else "{:.4f}") for c in cmp.columns if c.endswith(("acc", "Brier"))}
        st.dataframe(cmp.style.format(fmts, na_rep="—"), hide_index=True, width="stretch")
    mem = D.memorization_probe()
    st.subheader("What does the model remember about real prices?")
    if mem.empty:
        st.info("Run `python -m app.sandbox.walkforward --probe-memorization --model qwen3:8b` to create this.")
    else:
        st.altair_chart(alt.Chart(mem).mark_line(point=True).encode(
            x=alt.X("month:T"), y=alt.Y("median_abs_pct_error:Q", title="median % error recalling the price"),
            color="model:N"), width="stretch")
        st.caption("Higher error = less memorized. The test window starts in June 2025, after recall gets "
                   "clearly worse.")

# ---------------- new run ----------------
with tabs[6]:
    st.header("Start a new walk-forward run")
    if not D.DATA_CSV.exists():
        st.error("Price data missing. Run `python scripts/fetch_prices.py --end YYYY-MM-DD` first.")
    models = D.ollama_models()
    if not models:
        st.warning("Can't reach Ollama at 127.0.0.1:11434. Start it with `ollama serve`.")
    with st.form("new_run"):
        c1, c2, c3 = st.columns(3)
        models = models or ["qwen3:8b"]
        model = c1.selectbox("Model", models, index=models.index("qwen3:8b") if "qwen3:8b" in models else 0)
        target = c2.selectbox("Target", ["abs", "excess"],
                              format_func=lambda t: "Up vs down" if t == "abs" else "Beat the market (SPY)")
        new_tag = c3.text_input("Name (tag)", value="my_run")
        c4, c5, c6, c7 = st.columns(4)
        step = c4.number_input("Days between cutoffs", 5, 60, 5, help="5 = weekly, 10 = every two weeks")
        warmup = c5.number_input("Warm-up cutoffs", 0, 40, 12)
        reflect = c6.number_input("Reflect every N cutoffs", 1, 20, 4)
        start = c7.text_input("Start date", "2025-06-02")
        est = int(64 * 5 / step)
        st.caption(f"≈ {est} cutoffs × 40 LLM calls ≈ {est * 40:,} calls. qwen3:8b ≈ 2 calls/s, qwen3:14b ≈ 1 call/s "
                   "on a 5070. Identical prompts are served from the cache.")
        clash = any(r["tag"] == new_tag for r in runs)
        submitted = st.form_submit_button("Start run", type="primary")
    if submitted:
        try:
            if clash:
                raise ValueError(f"A run called '{new_tag}' already exists. Pick another name.")
            pid = D.launch_run(D.build_run_args(model, new_tag, target, int(step), int(warmup), int(reflect), start),
                               new_tag)
            st.success(f"Started `{new_tag}` (pid {pid}). It keeps running even if you close this page.")
        except ValueError as e:
            st.error(str(e))

    launched = D.ui_launched_tags()
    if launched:
        st.subheader("Runs started from here")
        if st.button("Refresh"):
            st.rerun()
        for t in reversed(launched):
            s = D.run_status(t)
            state = "❌ failed" if s["failed"] else "✅ finished" if s["finished"] else \
                "⏳ running" if s["running"] else "⏹ stopped"
            with st.expander(f"{t} — {state}", expanded=s["running"] or s["failed"]):
                if s["total"]:
                    st.progress(s["done"] / s["total"], text=f"cutoff {s['done']} of {s['total']}")
                st.code(s["log_tail"] or "(no output yet)", language=None)

# ---------------- live research ----------------
with tabs[7]:
    import asyncio
    import json

    from app.live.research import research_tickers

    st.header("Live, web-informed research")
    st.caption("The jailed agent reads current prices, news, articles and SEC filings through guarded tools, "
               "then gives P(up) with sources. Valid only for decisions made now: this can't be backtested "
               "honestly, so its accuracy is measured forward in time.")
    with st.form("live_form"):
        l1, l2, l3, l4 = st.columns([3, 1, 1, 1])
        tick_text = l1.text_input("Tickers (comma-separated)", "NVDA, JPM")
        live_models = D.ollama_models() or ["qwen3:8b"]
        live_model = l2.selectbox("Model", live_models,
                                  index=live_models.index("qwen3:8b") if "qwen3:8b" in live_models else 0)
        rounds = l3.number_input("Tool rounds", 1, 5, 3)
        horizon = l4.number_input("Horizon (days)", 1, 60, 5)
        go = st.form_submit_button("Research now", type="primary")
    if go:
        tickers = [x.strip().upper() for x in tick_text.split(",") if x.strip()][:8]
        status = st.status(f"Researching {', '.join(tickers)}…", expanded=True)

        def on_event(e: dict) -> None:
            if e.get("final"):
                status.write(f"**{e['ticker']}** done: P(up) = {e['p_up']:.2f} in {e['elapsed_s']} s")
            else:
                mark = "✅" if e["ok"] else "⚠️"
                status.write(f"{mark} `{e['ticker']}` {e['tool']} {json.dumps(e.get('args'))[:90]} "
                             f"· {e['elapsed_ms']} ms" + ("" if e["ok"] else f" · {e['error'][:80]}"))
        try:
            recs = asyncio.run(research_tickers(tickers, model=live_model, horizon=int(horizon),
                                                max_rounds=int(rounds), concurrency=3, on_event=on_event))
            status.update(label=f"Done: {len(recs)} decision(s)", state="complete", expanded=False)
        except (ValueError, RuntimeError, OSError) as e:
            status.update(label="Failed", state="error")
            st.error(str(e))
            recs = []
        for rec in recs:
            with st.container(border=True):
                a1, a2 = st.columns([1, 4])
                a1.metric(rec["ticker"], f"{rec['p_up']:.2f}", help=f"P(up in {rec['horizon_days']} trading days)")
                a1.caption(f"{rec['rounds']} rounds · {rec['tool_calls']} tools · {rec['elapsed_s']} s")
                a2.markdown(rec["reason"])
                for s in rec["sources"]:
                    a2.markdown(f"- {s}" if not s.startswith("http") else f"- [{s[:90]}]({s})")
                with st.expander("Tool trace"):
                    st.dataframe(pd.DataFrame([{"tool": x["tool"], "args": json.dumps(x["args"])[:120],
                                                "ok": x["ok"], "ms": x["elapsed_ms"], "chars": x["chars"],
                                                "error": x["error"] or ""} for x in rec["tool_log"]]),
                                 hide_index=True, width="stretch")

    past = D.live_decisions()
    st.subheader("Past live decisions")
    if past.empty:
        st.info("None yet.")
    else:
        st.dataframe(past.drop(columns="path").style.format({"p_up": "{:.2f}", "as_of": "{:%Y-%m-%d %H:%M}"}),
                     hide_index=True, width="stretch")
