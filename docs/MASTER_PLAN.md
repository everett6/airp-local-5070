# Master plan: from "nothing beats SPY" to a tested edge

Written 2026-09-25. Everything stays on free data and fake money until the gates below are passed. Real money is
the owner's decision alone, and only after Gate 3.

## 1. Where we are

- **The infrastructure works.** It has:
  - point-in-time data;
  - a jailed model;
  - date-limited internet tools;
  - a paper-trading simulator with costs;
  - beta and alpha, deflated Sharpe, and exact replay;
  - 337 tests.

  Few individual setups have this. Most published "AI beats the market" results fail exactly these checks.
- **Every signal so far has failed.** That covers v5, v6 and v7, 16 years of classic rules, and the LLM with
  internet research (−0.07 rank IC in the clean 2025–26 window). None has alpha distinguishable from zero.

## 2. Why it hasn't worked

1. **Wrong hunting ground.** Our universe is the 100 most-traded US stocks, the most efficient corner of the
   market. The documented anomalies that survive, such as post-earnings drift, live mostly in small and mid caps,
   where fewer algorithms trade.
2. **Wrong question.** "Will this stock beat the market over 5 or 20 days?", asked every month about every stock,
   is mostly noise. The published LLM successes are about **events**: reading earnings releases and financial
   statements when they come out. Kim, Muhn & Nikolaev (2024) found GPT-4 predicts the direction of future
   earnings from statements better than analysts, and trading on it gave higher Sharpe and alpha.
3. **Too few independent observations.** 20 months × 20 stocks gives a 95% CI of about ±15% a year: nothing can be
   proven. Earnings events give **thousands** of observations a year.
4. **One model doing everything:** searching, reading, summarising and deciding in an 8k context.

## 3. The pivot: an event-driven earnings reader

**Trigger.** Every 8-K with **Item 2.02** (results of operations): the earnings release. It comes from EDGAR,
free, with an exact acceptance timestamp, so point-in-time is exact. The universe is S&P 1500
(large + mid + small), about 6,000 events a year.

**Pipeline** (sub-agents, each with one job, each checked by code):

| stage | model | job | checked by |
|---|---|---|---|
| Fetcher | code only | pull the EX-99.1 press release, the prior quarter's release, and prices up to the filing time | timestamps (already built) |
| Extractor | small: qwen3:4b or 8b | turn the release into JSON: revenue, EPS, guidance, one-offs | **graded automatically against the SEC's XBRL numbers** (companyfacts, free); every number must appear in the text (`verify_brief`) |
| Surprise | code only | year-over-year change, guidance raised or cut vs last quarter, SUE | unit tests |
| Narrative reader | qwen3:8b | tone, guidance language, management changes, as tags plus quoted sentences | quotes must exist in the source |
| Decision | Bonsai 27B (or whichever wins the head-to-head) | read the numbers and tags; give P(beats sector over 20/60 days), BUY or PASS | log-odds ranking; the portfolio rule is code |

**Trading rule (code).**

- **Entry:** buy at the next open after the filing, hold 20 or 60 days (pre-registered), equal weight, cap per
  name.
- **Measurement:** market- and sector-neutral. We measure picks minus their sector ETF, so a strong market can't
  fake skill.

**Why this has a real chance:**

- a narrow, documented effect (post-earnings drift survives where attention is limited);
- a task LLMs are shown to do well (reading statements);
- thousands of events, so a small edge becomes visible within one year of data.

## 4. Models on a 12 GB RTX 5070

- **Smaller models for research and summaries: yes, but for focus, not speed.** 85% of a research decision is
  waiting on SEC and the Internet Archive; the model is about 15%. A 4B extractor frees VRAM and time for the
  decision model.
- **Sub-agents over one generalist.** Each gets a short prompt, a small context and a JSON schema. Each is scored on
  its own: the extractor against XBRL, the decision model by rank IC. A bad stage then shows up in its own numbers
  instead of hiding in the final return.
- **Only one model on the GPU at a time.** Two GPU jobs at once caused Xid 79. Run the stages as batches (extract
  everything, then decide everything). The GPU lock already enforces this.
- **Bonsai vs qwen3:8b as decision model: settle it by measurement.** Run both on the same 400 briefs, then the same
  events; keep the higher out-of-sample IC. Ternary-Bonsai-2-27B (5.9 GB) gets the same test once it loads in
  Ollama.
- **Training cutoff:** internet lookups don't erase what a model remembers. Gao, Jiang & Yan (2025) found that
  memorization inflates apparent LLM forecasting skill by about 37% in-sample, and the effect vanishes after the
  cutoff. We keep the clean-window rule and the LAP probe. Bonsai's recall of 2025 is poor (it said the 2024
  election hadn't happened), which helps.

## 5. Sandbox: NVIDIA OpenShell

Run the fetcher and extractor inside an OpenShell sandbox with deny-by-default egress. Allow only:

- `www.sec.gov`, `data.sec.gov` and `efts.sec.gov`;
- `en.wikipedia.org` and `web.archive.org`;
- the local Ollama endpoint.

This is enforced at the network layer as well as by our Python gateway, so a prompt-injected page can't send data
anywhere else. The policy is hot-reloadable, and every blocked request is logged.

## 6. Gates

These gates are pre-registered in `docs/EXECUTION_PLAN.md` before any run looks at results.

1. **Gate 1: backtest in the clean window (2025-01 → 2026-09).**
   - Event-level rank IC > 0 with its 95% CI excluding 0.
   - The long-minus-sector spread beats costs (5 bp per side).
   - The decision model beats the code-only surprise rule, because otherwise the LLM adds nothing.
2. **Gate 2: forward paper trading for 3–6 months.**
   - The same code runs on new filings as they arrive, with live tools and no parameter changes.
   - The same criteria apply.
   - It needs the PC on at filing times, or a manual daily run.
3. **Gate 3: owner's decision.** Only if Gates 1 and 2 pass does real money come up at all, sized so a total loss
   is affordable.

**Realistic target:** a working event signal is typically worth a few % a year of alpha, not 66%. The plan is built
to find out cheaply whether we have one.

## 7. Order of work

| step | what | compute |
|---|---|---|
| 1 | Finish the Bonsai-vs-qwen3:8b head-to-head on the 400 research briefs (in progress) | ~1–2 h GPU |
| 2 | Event dataset: every Item 2.02 8-K for S&P 1500 names, 2024-01 → 2026-09, plus prices | network, ~hours |
| 3 | Extractor sub-agent + automatic XBRL grading; pick the smallest model that is ≥ 95% accurate | ~2 h GPU |
| 4 | Code-only surprise rule (the baseline the LLM must beat) | CPU, minutes |
| 5 | Decision stage + event-level evaluation; freeze and pre-register | ~3–5 h GPU |
| 6 | OpenShell policy for the research sandbox | setup |
| 7 | Gate 2 forward runner (manual daily command; no background service unless asked) | — |

## 8. Also worth considering

The infrastructure itself is a product: point-in-time LLM backtesting with leak checks, date-limited agent tools,
and exact replay. If no trading edge appears, that tooling is still valuable to funds and researchers who need to
test LLM strategies honestly.

## Sources

- Gao, Jiang & Yan, *Detecting Lookahead Bias in LLM Forecasts*: https://arxiv.org/abs/2512.23847
- Glasserman & Lin, *Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis*:
  https://arxiv.org/abs/2309.17322
- Kim, Muhn & Nikolaev, *Financial Statement Analysis with Large Language Models*: https://arxiv.org/abs/2407.17866
- *Is post-earnings announcement drift a thing again?* (UCLA Anderson Review):
  https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/
- NVIDIA OpenShell policies: https://docs.nvidia.com/openshell/sandboxes/policies
