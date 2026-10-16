# New research directions for the Jan → Bonsai stock research (16 Oct 2026)

This is a literature scan only. Nothing was run, and none of these is a test until it is pre-registered in
PLAN_60_V2. Every idea below was checked against `trials_registry.jsonl` (55 trials) and the plan docs; none has
been tried. The selection follows the 28 Sep lesson from B/B2/B3: the next version must change what is scored, not
add more evidence before the release.

## What our own files already show (read 16 Oct, no models run)

Latest decided call per ticker (197 tickers, mostly 7 Oct):

| Horizon | Bull | Bear | Bull share |
|---|---|---|---|
| day | 164 | 33 | 83% |
| medium | 170 | 27 | 86% |
| long | 170 | 27 | 86% |
| short | 47 | 150 | 24% |

**The side depends more on the horizon than on the company.** The same company is mostly bull on day, medium and
long, and mostly bear on short. 150 of 197 cards are longer than 20,000 characters. Both patterns match two 2026
findings: long contexts make a model ignore evidence it read, and models anchor on their template.

## Ranked ideas

### 1. Does Bonsai's verdict use the evidence at all? (cheap; fixes validity first)
- **Literature:**
  - Liu & Liu, *Reading Is Not Using* (arXiv 2608.24842, Aug 2026). As unrelated context grows, a risk
    disclosure's influence on the judgment falls to the noise floor, even though the model can still retrieve it.
    A short, structured restatement of the evidence placed next to the decision restores its influence.
  - *Fin-Bias* (arXiv 2605.09106) supplies the counterfactual design: remove the key sentence, or swap it for its
    opposite.
  - *Your AI, Not Your View* (arXiv 2507.20957) finds models cling to their first judgment as counter-evidence grows.
- **Test for us:** take saved cards (the 167 frozen for BZ2 work) and make three versions of each:
  - the original;
  - the card with its strongest negative facts removed;
  - the card with one key fact flipped.

  Measure how often the side changes. If it barely moves, the judge isn't using the evidence. Then try one fix: a
  bull-facts / bear-facts table, built by code from the checked facts, placed just before the question.
- **Why first:** no return outcomes are needed, so it can be scored the same day. It explains the bullish skew, and
  every other idea depends on the judge reading its evidence. Cost: about 1–2 GPU hours on saved cards.

### 2. An LLM-built supply-chain graph for customer momentum (LLM extracts, code predicts)
- **Literature:**
  - Cohen & Frazzini (2008): when a customer's stock moves, its suppliers' stocks follow with a lag. The suppliers
    are the ones bought or sold, because investors miss the published links between the firms.
  - *LLM Latent Edge Measurement* (arXiv 2607.15640, Jul 2026): an LLM pulls typed, directed, point-in-time
    links (supplier, customer concentration, licensing, cloud dependency) from 10-Ks and 10-Qs. An audit confirmed
    88% of them.
  - *Supply Chain Propagation of Textual Signals* (arXiv 2606.29290, Jun 2026), single-author preprint: 10-K text
    signals spread over a supply-chain graph give Sharpe 0.86 and a 7.3%/yr FF5 alpha (t = 2.30) over 2011–2025.
- **For us:**
  - **Jan's job:** read each company's 10-K "major customers" note (customers above 10% of revenue must be
    disclosed) and Item 1. It writes links with quotes, and code checks each quote against the filing.
  - **Code's job:** the signal is last month's return of each supplier's customers.
  - All data is free from EDGAR.
- **Why it's promising:** the model never predicts a return, so memorizing past prices can't leak into the score.
  The links are dated by filing, so a long history can be tested honestly. That is what our previous AI tests
  lacked.
- **Risks:** the effect is published and may have decayed (Moers 2013). Coverage may be thin for firms that
  disclose no major customers.

### 3. "Lazy prices" / moving targets in filings, using Jan as the extractor
- **Literature:**
  - Cohen, Malloy & Nguyen, *Lazy Prices* (2020): firms whose 10-K or 10-Q language changes a lot earn lower
    returns later. The market reacts only when the news comes out.
  - *From Text to Alpha* (arXiv 2510.03195, rev. Mar 2026): an LLM extracts the metric phrases a firm stresses, and
    an embedding measures how far it drifts from them the next period. The authors report more than twice the alpha
    of a keyword baseline.
- **For us:**
  - **Jan's job:** extract the KPI phrases from consecutive 10-Qs.
  - **Code's job:** an embedding model scores drift (Qwen3-Embedding is now allowed; downloading it needs your OK),
    and code turns the score into a monthly long-short signal.
  - The data is free EDGAR. Again the model does no forecasting.

### 4. Use confidence to choose calls, not to size them (analysis only, on calls already made)
- **Literature:**
  - *Ordinal Gates, Cardinal Bets* (arXiv 2609.00187, Sep 2026): an LLM's confidence can be good enough to rank
    calls and still be wrong in magnitude, so it suits a cut-off but not a size multiplier.
  - *FinAbstain* (arXiv 2607.24875) tests trading only confident calls, though so far only on simulated data.
- **For us:** `auto_trader.size()` doubles strong calls, which treats confidence as a size. Test whether "strong"
  beats "weak" on matured forward calls (`fund_report` already has `calibration_by_label`). If it ranks but doesn't
  scale, switch to trading only the top-ranked calls, at equal size. Needs a pre-registration and no new model runs.

### 5. A rule for any historical test: keep memorization out of the score (process, not a strategy)
- **Literature:**
  - Gao, Jiang & Yan (arXiv 2512.23847, Jan 2026): LLM headline forecasts work mostly through memorization and
    collapse after the model's training cutoff.
  - Lopez-Lira, Tang & Zhu (SSRN 5217505, Apr 2026): masking names and dates often fails to stop recall.
  - Point-in-time models exist (ChronoGPT vintages on Hugging Face, 1.5B parameters, 1,792-token context), but they
    are too small for our cards.
- **Rule:** in any backtest before the models' cutoffs, the LLM may extract (ideas 2 and 3) but never forecast.
  Forecasting is tested forward only. This also means the B/B2/B3 failures on 2024 ran with possible memorization
  in the models' favor, so they are strong negatives.

### Lower priority
- **LLM optimism as a contrarian signal.** Chen, Green, Gulen & Zhou (arXiv 2409.11540) and Bybee (arXiv
  2305.02823) find LLM return expectations extrapolate past returns and are too optimistic. Bybee finds them
  negatively correlated with later returns. Flipping our own calls after seeing outcomes would be data snooping, so
  it would have to be registered as a market-level indicator from the start.
- **LLM-mined formula factors** (QuantaAlpha 2602.07085; AlphaMemo 2606.20625, IC about 0.04 on the S&P 500 over
  2022–25). These are self-reported backtests with a heavy multiple-testing burden, and many of our classic factor
  sleeves already failed.
- **News novelty from research memory** (stored facts per ticker). Evidence that novelty predicts returns is old and
  market-level ("New News is Bad News"), so this is weak.

## Suggested order
1. Idea 1 (evidence-use audit): same day, about 1–2 GPU hours on saved cards.
2. Idea 4 (analysis only).
3. Idea 2 (supply chain): the biggest build. It needs EDGAR 10-K parsing and Jan link extraction over several
   years of filings, which means many GPU hours, run around Night.
4. Idea 3 (lazy prices): needs an embedding model download (needs your OK).

Each needs a pre-registration pushed before any code runs, and runs once.
