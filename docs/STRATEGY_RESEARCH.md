# Systematic strategy research: is ~66% a year defensible?

Written 2026-09-26 as a skeptical literature review for this project (paper money, free data, one PC). Tags:
**[PR]** peer-reviewed, **[WP]** working paper / preprint, **[PRAC]** practitioner or fund research.
A number marked *(not re-checked)* comes from memory of the paper and should be verified before it is used anywhere.

## Bottom line (plain language)

- **No peer-reviewed evidence supports 66% a year for an individual at reasonable risk.** The only well-documented
  record near it is Renaissance's Medallion fund: 63.3% compounded **gross of fees**, 1988–2018, with a Sharpe above 2
  ([Cornell 2020, JPM] [PR], from Zuckerman's 2019 book). It is closed to outsiders, capped at about $10B, and runs on
  infrastructure no individual has.
- **The maths:** a steady 66% a year needs a Sharpe of about **1.7 at 30% volatility**, or **2.4 at 20%**. With any
  leverage at all, it needs a Sharpe of at least **0.97**, and then only by running near 100% volatility.
- **What the best strategies actually show:**
  - The best diversified, documented strategies show Sharpe ratios of about 0.4–1.2 *before* the usual
    60–75% haircut from backtest to live trading.
  - At those Sharpe ratios, 66% requires leverage that implies **losing 57–81% from a peak** (median, 10-year
    simulation), even if the backtest Sharpe were true.
- **A defensible target:**
  - A diversified, cost-aware, volatility-targeted book with a real Sharpe of 0.5–0.8.
  - At 10–15% volatility that is roughly 8–15% a year (cash plus 5–10%).
  - Mild leverage could lift it to 15–25% with drawdowns of 25–40%.
  - Anything above that is a bet on an unproven edge.
- **This project's own lead** (the 1-week book: 21.4% a year, Sharpe 1.24) comes from about 2.5 years:
  - The Sharpe's standard error over that span is about ±0.84, so its 95% interval runs from roughly −0.4 to 2.9.
  - The 2024 IC (+0.027) is much weaker than 2025-26 (+0.103).
  - It is a lead, not evidence of 66%.

## Part 1. What 66% requires

Log growth needed: ln(1.66) = 0.507 a year. With return volatility σ and excess Sharpe S, long-run log growth is about
r + Sσ − σ²/2. The risk-free rate r is taken as 4%; financing spreads, fees and fat tails are ignored, so every number
below is optimistic. Script: `scripts/target66.py`, results reproduced below.

| Portfolio volatility | Sharpe needed (net of all costs) |
|---|---|
| 10% | 4.72 |
| 15% | 3.19 |
| 20% | 2.43 |
| 25% | 1.99 |
| 30% | 1.71 |
| 40% | 1.37 |
| 50% | 1.18 |
| 80–100% | 0.97–0.98 (full Kelly: the least Sharpe that can ever reach 66%) |

**Hard constraint:** below a Sharpe of about 0.97, *no* amount of leverage reaches 66%. Growth peaks at full Kelly,
at r + S²/2:

| Sharpe | Best possible growth | At volatility |
|---|---|---|
| 0.5 | ~18% | 50% |
| 0.8 | ~43% | 80% |

**Leverage needed** on a base strategy, borrowing at the risk-free rate + 1.5%:

| Base strategy | Leverage needed | Resulting volatility |
|---|---|---|
| 10% vol, Sharpe 0.5, 0.8 or 1.0 | impossible at any leverage | — |
| 10% vol, Sharpe 1.2 | 6.1× | 61% |
| 10% vol, Sharpe 1.5 | 3.9× | 39% |
| 15% vol, Sharpe 1.2 | 3.7× | 55% |

**Monte Carlo**, 10 years, daily, fat tails (Student-t with 4 degrees of freedom, i.i.d.). Real strategies cluster
their losses, so these drawdowns are *understated*. Each row sets the volatility so that the backtest Sharpe gives 66%;
the second line of each pair assumes the true Sharpe is half the backtest (Part 3 explains why).

| Backtest Sharpe, vol | True Sharpe | Median CAGR | Median max DD | 90th pct max DD | Median worst year | P(max DD > 80%) |
|---|---|---|---|---|---|---|
| 3.2, 15% | 3.2 | 66% | 12% | 17% | +33% | 0% |
| | 1.6 | 31% | 17% | 23% | +4% | 0% |
| 2.4, 20% | 2.4 | 65% | 18% | 25% | +22% | 0% |
| | 1.2 | 30% | 25% | 35% | −4% | 0% |
| 1.7, 30% | 1.7 | 66% | 31% | 43% | +7% | 0% |
| | 0.85 | 28% | 42% | 57% | −18% | 0% |
| 1.2, 50% | 1.2 | 67% | 57% | 72% | −21% | 3% |
| | 0.6 | 24% | 69% | 86% | −41% | 21% |
| 1.0, 80% | 1.0 | 67% | 81% | 93% | −51% | 54% |
| | 0.5 | 13% | 91% | 99% | −67% | 85% |

Reading: 66% with a tolerable drawdown needs a *true, net* Sharpe of 2 or more. Part 2 shows that nothing an individual
can build has credible evidence of that. With a realistic Sharpe, levering up to hit 66% mostly buys a large chance
of an 80%+ loss.

## Part 2. Literature by strategy family

### 1. Time-series momentum (trend following)

- **Moskowitz, Ooi, Pedersen, "Time Series Momentum", JFE 104(2), 2012** [PR]
  - Link: [sciencedirect](https://www.sciencedirect.com/science/article/pii/S0304405X11002613)
  - Markets and period: 58 liquid futures (equity indexes, bonds, currencies, commodities), 1985–2009.
  - Rule: go long if the past 12-month excess return is positive, short if negative, each position scaled to a
    constant volatility.
  - Findings: all 58 contracts show positive TSMOM, and 52 of them are significant at 5%. The diversified portfolio
    earns a large alpha with little exposure to standard factors, and does best in extreme markets.
  - Replication: yes, see the next paper.
- **Hurst, Ooi, Pedersen, "A Century of Evidence on Trend-Following Investing", AQR 2012 / JPM 2017** [PRAC→PR]
  - Link: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026)
  - Rules: 1-, 3- and 12-month trends, 10% volatility target.
  - Costs: AQR's estimates, six times higher before 1992 and twice as high in 1993–2002, plus simulated 2-and-20
    fees. For 2003–12, one-way costs are 6 bps (equities), 1 bp (bonds), 10 bps (commodities) and 3 bps
    (currencies).

  | Period | Gross | Net of 2/20 | Volatility | Net Sharpe |
  |---|---|---|---|---|
  | 1903–2012 | 20.0% | 14.3% | 9.9% | 1.00 |
  | 1973–82 (best decade) | — | — | — | 1.89 |
  | 1983–92 | — | — | — | 0.53 |
  | 2003–12 | 11.4% | 7.5% | — | 0.61 |

  - Worst net drawdown: −26.3% (1947–48, 84 months to recover); 2009 drawdown −13.5%.
  - The authors themselves use a "conservative" forward net Sharpe of 0.4.
- **Babu, Levine, Ooi, Pedersen, Stamelos, "You Can't Always Trend When You Want", JPM 2020** [PR/PRAC]
  - Link: [pdf](https://www.belmontinvestments.com/cimg/file/articles/53/pdf/190425aqrtrendfollowing.pdf)
  - Finding: the Sharpe fell sharply after the 2008 crisis because big, sustained trends were rarer.

**Verdict.** The evidence is among the best in finance: out of sample across a century, many markets and many
research groups. It is a Sharpe ~0.4–0.8 net diversifier, not a high-return engine, so it sits in **category B**.
To reach 66% it would need about 100% volatility, and it cannot get there at all if the true Sharpe is below 0.97.

### 2. Cross-sectional momentum (stocks)

- **Jegadeesh & Titman, "Returns to Buying Winners and Selling Losers", JF 48(1), 1993** [PR]
  - Link: [doi](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)
  - US stocks, 1965–89; buy past 3–12-month winners and short the losers, earning about 1% a month *(not
    re-checked)*.
  - Replication: many times, and in most countries except Japan. It also works out of sample in 1990+
    (JT 2001, *not re-checked*).
- **Daniel & Moskowitz, "Momentum Crashes", JFE 122(2), 2016** [PR]
  - Link: [sciencedirect](https://www.sciencedirect.com/science/article/pii/S0304405X16301490)
  - Crashes: winners-minus-losers lost **−91.6% over two months in 1932** and **−73.4% over three months in 2009**.
  - Crashes come in "panic states", after market falls and when volatility is high, as the market rebounds.
  - A dynamic version about doubles the Sharpe (in sample).
- **Barroso & Santa-Clara, "Momentum Has Its Moments", JFE 116(1), 2015** [PR]
  - Link: [repec](https://ideas.repec.org/a/eee/jfinec/v116y2015i1p111-120.html)
  - Scaling momentum by its 6-month realized volatility raises the Sharpe from 0.53 to 0.97, cuts excess kurtosis
    from 18.2 to 2.7, and reduces skew from −2.47 to −0.42.
- **Weaknesses**
  - Momentum turns over heavily. Novy-Marx & Velikov (below) find mid-turnover anomalies cost 20–57 bps a month,
    often more than half their gross spread.
  - Short-selling losers is costly and constrained.
  - Momentum decays after publication (see McLean & Pontiff under multiple testing).

**Verdict.** Category B. It is robust as a factor but has a crash tail, and needs volatility scaling plus a cost-aware
implementation.

### 3. Value, 4. Quality/profitability, and 8–9. combinations and multi-factor portfolios

- **Fama & French, "Common risk factors in the returns on stocks and bonds", JFE 33(1), 1993** [PR]
  - Link: [doi](https://doi.org/10.1016/0304-405X(93)90023-5)
- **Novy-Marx, "The Other Side of Value: The Gross Profitability Premium", JFE 108(1), 2013** [PR]
  - Link: [doi](https://doi.org/10.1016/j.jfineco.2013.01.003)
  - Gross profits over assets predicts returns about as strongly as book-to-market, and it hedges value.
- **Asness, Frazzini, Pedersen, "Quality Minus Junk", Review of Accounting Studies 24, 2019** [PR]
  - Link: [doi](https://doi.org/10.1007/s11142-018-9470-2)
- **Asness, Moskowitz, Pedersen, "Value and Momentum Everywhere", JF 68(3), 2013** [PR]
  - Link: [wiley](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12021)
  - Markets: stocks in 4 regions, country indexes, bonds, currencies and commodities, 1972–2011.
  - Value and momentum are negatively correlated in every market, so a 50/50 mix beats either alone.
  - The all-asset combination's gross Sharpe was about 1.4–1.6 *(not re-checked)*, before costs, on long-short
    portfolios.
- **Baltussen, Swinkels, van Vliet, "Global Factor Premiums", JFE 142(3), 2021** [PR]
  - Link: [sciencedirect](https://www.sciencedirect.com/science/article/pii/S0304405X21003007)
  - 24 factors across equities, bonds, commodities and currencies, **1800–2016**.
  - Replication in their p-hacking-aware framework is ambiguous, but out-of-sample tests show most premiums are robust
    with limited decay.

**Weaknesses**

- Value had a deep 2018–2020 drawdown (public record for the HML factor in the Ken French library).
- Long-short implementations need shorting.
- Long-only factor tilts capture only part of the premium.

**Verdict.** Category B. Individual factor Sharpe ratios are about 0.3–0.7; diversified combinations are higher on
paper and much lower after the backtest-to-live haircut.

### 5. Carry

- **Koijen, Moskowitz, Pedersen, Vrugt, "Carry", JFE 127(2), 2018** [PR]
  - Link: [doi](https://doi.org/10.1016/j.jfineco.2017.11.002)
  - Assets: global equities, 10-year bonds, curve slope, Treasuries, commodities, currencies, credit and options.
    The global carry factor sample is 1983–2012.
  - Carry Sharpe ratios by asset class (Table 1):

  | Asset class | Carry Sharpe |
  |---|---|
  | Equities | 0.91 |
  | Bond level | 0.52 |
  | Curve slope | 1.03 |
  | Commodities | 0.60 |
  | Currencies | 0.68 (skew −0.68) |
  | Puts | 1.80 (skew −1.75) |
  | Short volatility | 0.33 (skew −7.07, kurtosis 75.6) |
  | **Diversified global carry factor** | **1.20** (7.18% mean, 5.96% volatility) |

  - The three biggest global carry drawdowns (1972–75, 1980–82, Aug 2008–Feb 2009) coincide with global recessions,
    and all carry strategies lose together then.
- **Brunnermeier, Nagel, Pedersen, "Carry Trades and Currency Crashes", NBER Macro Annual 23, 2008** [PR]
  - Currency carry has negative skew and crashes when funding liquidity dries up *(not re-checked)*.

**Verdict.** Category B, with crash risk hidden in a smooth return stream. The 1.2 Sharpe is gross, and futures or FX
access is needed; ETF proxies are poor.

### 6. Mean reversion / statistical arbitrage

- **Gatev, Goetzmann, Rouwenhorst, "Pairs Trading", RFS 19(3), 2006** [PR]
  - Link: [doi](https://doi.org/10.1093/rfs/hhj020)
  - US stocks, 1962–2002, positive excess returns *(size not re-checked)*.
  - Later work (Do & Faff, FAJ 2010) finds profits falling over time *(not re-checked)*.
- Short-term reversal is the strategy most limited by trading costs in AQR's live execution data
  ([Frazzini, Israel, Moskowitz, "Trading Costs"] [WP/PRAC](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719)).

**Verdict.** Mostly category D for a retail-cost, daily-bar trader: the edge lives in execution speed and costs you do
not have.

### 7. Volatility targeting

- **Moreira & Muir, "Volatility-Managed Portfolios", JF 72(4), 2017** [PR]
  - Link: [doi](https://doi.org/10.1111/jofi.12513)
  - Scaling factor exposure by inverse variance gives large alphas in spanning regressions.
- **Cederburg, O'Doherty, Wang, Yan, "On the performance of volatility-managed portfolios", JFE 138(1), 2020** [PR]
  - Link: [SSRN](https://www.ssrn.com/abstract=3357038)
  - Across **103 equity strategies**, volatility-managed versions do *not* systematically beat the unmanaged ones
    out of sample. Realistic real-time versions usually have *lower* Sharpe ratios, because the spanning regressions
    are unstable.

**Verdict.** It is a risk-control tool, not an alpha source. It reliably trims tails for momentum (Barroso &
Santa-Clara) and keeps leverage honest. Do not count on it to raise the Sharpe.

### 10. Multi-asset futures

This family is covered by families 1, 5 and 8. For one person it is the most *diversified* route, but it needs a
futures account, margin, roll handling and continuous-contract data.

### 11. Options (variance risk premium)

- **Bondarenko, "Why Are Put Options So Expensive?", QJF 4(3), 2014** [PR]
  - Link: [doi](https://www.worldscientific.com/doi/10.1142/S2010139214500153)
  - S&P 500 puts were historically overpriced relative to any model in a broad class.
- **Koijen et al. (above):** put carry has a Sharpe of 1.80 but skew −1.75, and short volatility has kurtosis 75.6.
- **Tail event (public record):** on 5 Feb 2018 the short-volatility ETN XIV lost about 96% in one day and was
  terminated.

**Verdict.** It has a real premium, but with the worst tail of any family here. It looks best exactly until it
blows up. Levered short volatility is the classic **category C** route to a huge backtest CAGR.

### 12. Event-driven and macro (the project's current approach)

- **Bernard & Thomas, "Post-Earnings-Announcement Drift", JAR 27, 1989** [PR]
  - Link: [doi](https://doi.org/10.2307/2491062)
  - Prices keep drifting after earnings surprises.
- **Chordia, Goyal, Sadka, Sadka, Shivakumar, "Liquidity and the Post-Earnings-Announcement Drift", FAJ 65(4), 2009**
  [PR]
  - Link: [tandfonline](https://www.tandfonline.com/doi/abs/10.2469/faj.v65.n4.3)
  - The long-short PEAD return is **0.04% a month in the most liquid stocks** versus 2.43% in the least liquid.
    Transaction costs eat **70–100%** of the paper profits.
- **Martineau, "Rest in Peace Post-Earnings Announcement Drift", Critical Finance Review 11(3–4), 2022** [PR]
  - Link: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3111607)
  - **PEAD has not existed in large stocks since 2006**; prices now absorb the surprise on the announcement day.
  - Implication for this project: an S&P 500 earnings signal has to capture something *beyond* the surprise, such as
    tone, guidance or context. That is exactly what the Bonsai tests must show, and it is also why the prior for the
    effect should be skeptical.
- **Welch & Goyal, "A Comprehensive Look at the Empirical Performance of Equity Premium Prediction", RFS 21(4), 2008**
  [PR]
  - Link: [OUP](https://academic.oup.com/rfs/article-abstract/21/4/1455/1565737)
  - Classic market-timing predictors did poorly both in and out of sample; macro timing of the index sits in
    **category D**.

### 13. Machine learning and LLMs

- **Gu, Kelly, Xiu, "Empirical Asset Pricing via Machine Learning", RFS 33(5), 2020** [PR]
  - Link: [OUP](https://academic.oup.com/rfs/article/33/5/2223/5758276)
  - Data: US stocks from March 1957, with an out-of-sample test over 1987–2016.
  - The neural-network long-short decile portfolio has an out-of-sample Sharpe of **1.35 value-weighted** and **2.45
    equal-weighted**.
  - For the value-weighted version, the maximum drawdown is 31–62% depending on the model, and monthly turnover is
    about 110–150%.
  - Costs are not deducted.
- **Avramov, Cheng, Metzker, "Machine Learning vs. Economic Restrictions", Management Science 69(5), 2023** [PR]
  - Link: [INFORMS](https://pubsonline.informs.org/doi/abs/10.1287/mnsc.2022.4449)
  - Excluding **microcaps, distressed stocks or high-volatility periods considerably weakens** deep-learning profits,
    and reasonable trading costs erode them further.
  - The equal-weighted 2.45 is largely a microcap and cost artifact, which puts it in **category C**.
- **Lopez-Lira & Tang, "Can ChatGPT Forecast Stock Price Movements?"** [WP]
  - Link: [arXiv](https://arxiv.org/abs/2304.07619)
  - GPT-4 news scores predict next-day returns, and more so in small stocks and on negative news.
  - The strategy makes **+350% cumulative at 10 bps per trade but only +50% at 25 bps**.
  - Returns **fall as LLM adoption rises**.
- **Glasserman & Lin, "Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis"**
  [WP]
  - Link: [arXiv](https://arxiv.org/abs/2309.17322)
  - A backtest is biased when the LLM's training period overlaps the test period.
  - Anonymizing company names *improved* results, meaning the "distraction" from the model's prior knowledge of a
    company matters more than direct look-ahead.

**Verdict.** There is real out-of-sample evidence for ML in liquid stocks, at Sharpe ≈ 1–1.4 gross on long-short
portfolios. The evidence for LLM text signals is short, cost-sensitive and decaying.

### 14. Other families

- **Factor momentum.** Ehsani & Linnainmaa, JF 77(3), 2022 [PR]
  ([wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13131)): the average factor earns 51 bps a month
  after a positive year and 6 bps after a negative one, and factor momentum explains individual-stock momentum.
- **Betting against beta.** Frazzini & Pedersen, JFE 111(1), 2014 [PR]
  ([doi](https://doi.org/10.1016/j.jfineco.2013.10.005)). A later critique argues much of it is an equal-weighting and
  microcap effect *(not re-checked)*.
- **Crypto.** Liu & Tsyvinski, "Risks and Returns of Cryptocurrency", RFS 34(6), 2021 [PR]
  ([OUP](https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024)): strong time-series momentum and
  attention effects, with no exposure to standard stock or macro factors.
  - Weakness: a short history dominated by one boom-bust regime. Any 66%+ crypto CAGR is **category C**, sample
    concentration.

### Multiple testing, decay and overfitting (applies to everything above)

| Paper | Finding |
|---|---|
| Harvey, Liu, Zhu, RFS 29(1), 2016 [PR] ([OUP](https://academic.oup.com/rfs/article/29/1/5/1843824)) | At least 316 factors had been tested; a new one should clear **t > 3.0**, not 2.0 |
| McLean & Pontiff, JF 71(1), 2016 [PR] ([wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365)) | Across 97 predictors, returns are **26% lower out of sample and 58% lower after publication** |
| Hou, Xue, Zhang, RFS 33(5), 2020 [PR] ([OUP](https://academic.oup.com/rfs/article-abstract/33/5/2019/5236964)) | With microcaps controlled, **65% of 452 anomalies fail t = 1.96**, and **82% fail t = 2.78** |
| Jensen, Kelly, Pedersen, JF 78(5), 2023 [PR] ([wiley](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249)) | Of 153 factors across 93 countries, **82.4% replicate** under a Bayesian framework (free data at [jkpfactors.com](https://jkpfactors.com/)) |
| Chen & Zimmermann, CFR 11(2), 2022 [PR] ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3604626)) | Reproduces 319 predictors, and 98% of the clearly significant ones reach t > 1.96 (free signals at [openassetpricing.com](https://www.openassetpricing.com/)) |
| Suhonen, Lennkh, Perez, JPM 43(2), 2017 [PR] ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2757113)) | Across 215 bank "alternative beta" strategies, the **median Sharpe fell 73% from backtest to live**; the most complex strategies fell 30+ points more |
| Novy-Marx & Velikov, RFS 29(1), 2016 [PR] ([OUP](https://academic.oup.com/rfs/article/29/1/104/1844518)) | Anomalies with monthly turnover under 50% mostly survive costs; a buy/hold band is the best simple cost fix |
| Frazzini, Israel, Moskowitz [WP] ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719)) | From live AQR trades, institutional costs are about a tenth of earlier academic estimates. That applies to an algorithmic institutional desk, not to retail market orders |
| Bailey & López de Prado, "The Deflated Sharpe Ratio", JPM 40(5), 2014 [PR] ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)) | Deflates a Sharpe for the number of trials, non-normal returns and sample length |
| Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting", J. Comp. Finance 20(4), 2017 [PR] ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)) | Combinatorially symmetric cross-validation gives the probability that the in-sample winner is below the median out of sample |

The literature disagrees about how bad the replication problem is. HXZ and HLZ are pessimistic; JKP and
Chen–Zimmermann are more optimistic. Both sides agree on two points: effect sizes shrink, and small/illiquid stocks
inflate results.

## Part 3. The 66% classification

| Class | Families |
|---|---|
| **A.** High returns without extreme leverage | **None accessible to an individual.** Medallion (63.3% gross, Sharpe > 2) is the lone credible example, and it is closed, capacity-capped and needs high-frequency infrastructure |
| **B.** Moderate returns that could be levered | Trend following, multi-asset carry, value + momentum + quality combinations, risk-managed momentum, factor momentum, liquid-stock ML. All have Sharpe ≈ 0.4–1.2 gross; reaching 66% needs 50–100% volatility, with a median drawdown of 57–81% even if the Sharpe holds |
| **C.** 66% is an artifact | Levered short volatility before 2018; equal-weighted ML long-short (microcaps); crypto in 2013–2021; LLM news strategies at zero cost over 1–3 years; **this project's 2025-26 quick book taken on its own** |
| **D.** No credible support | Retail intraday technical rules, market timing with macro predictors (Welch & Goyal), pairs trading at retail costs, PEAD in large caps after 2006 |

## Part 4. Shortlist (not ranked)

Ranges are indicative. "Hist." means historical and gross unless marked; every figure is before the 50–75%
backtest-to-live haircut.

| Strategy | Markets | Hold | Hist. excess return | Vol | Sharpe | Max DD | Turnover | Cost sensitivity | Evidence | Main failure mode |
|---|---|---|---|---|---|---|---|---|---|---|
| Trend following (TSMOM) | 20–60 futures, or ETF proxies | weeks–months | 5–14% net at 10% vol | 10% (targeted) | 0.4–1.0 net | 15–30% | medium | low–medium | very high (century, many groups) | trendless decades (2009–19), sharp reversals |
| Multi-asset carry | FX, bonds, commodities, equity index futures | ~1 month | ~7% at 6% vol | 6–10% | 0.5–1.2 | severe in recessions | medium | medium | high (one main group) | synchronized crash in crises; negative skew |
| Value + momentum + quality stocks | US/intl stocks (long-short or long-only tilt) | 1–12 months | 3–8% long-short | 8–15% | 0.5–1.0 combined | 20–40% (value 2018–20) | medium–high | medium | high, but decays after publication | factor droughts, crowding, momentum crashes |
| Risk-managed momentum | stocks or futures | 1 month | ~10–15% long-short (not re-checked) | ~12% (targeted) | ~0.97 in sample (US, 1927–2011) | lower than raw momentum | high | high | medium–high | crashes faster than the volatility estimate reacts |
| Factor momentum | factor portfolios (free from French/AQR/JKP) | 1 month | ~5% (not re-checked) | ~5–8% | 0.5–0.9 (not re-checked) | moderate | medium | medium | medium–high (JF 2022) | depends on the factor set chosen; turnover |
| Earnings/LLM text (current project) | S&P 500 around earnings | 1–5 days | unknown; 2025-26 sim 21% a year total (not excess) | ~15–20% | 1.24 (about 2.5 years, CI ≈ −0.4 to 2.9) | unknown | very high | **very high** (Lopez-Lira: 10→25 bps cuts returns ~85%) | low (short, LLM look-ahead risk) | decay as LLMs spread; PEAD gone in large caps; overfit books |
| Crypto trend (sleeve) | BTC/ETH spot | weeks | very high in sample, one regime | 50–80% | 0.5–1.5 (not re-checked) | 50–80% | low–medium | low | medium (short history) | regime change, exchange/custody risk |
| Volatility-targeting overlay | any sleeve | daily–weekly | ≈ 0 alpha | sets vol | ± small | cuts tails | low | low | high (as a risk tool), mixed as alpha | volatility jumps faster than the estimator |
| Index put-writing (only if the simulator has options) | SPX/SPY options | 1 month | 3–8% | 10–15% | 0.4–0.8 (not re-checked) | 30–40%+ (2008, 2020) | medium | medium | medium–high | crash tail (1987, 2008, Feb 2018) |

## Part 5. Research pipeline for the shortlisted families

1. **Data (free only)**
   - Daily prices: Yahoo/Stooq for stocks and ETFs.
   - Factors: Ken French library, AQR datasets (TSMOM, Century of Factor Premia, QMJ, BAB, VME), JKP global factors,
     Open Source Asset Pricing signals.
   - Fundamentals: SEC EDGAR/XBRL (already used here).
   - Rates: FRED. Positioning: CFTC Commitments of Traders.
   - Futures: free continuous contracts are unadjusted front-month series. Roll gaps must be removed (back-adjusted)
     or treated as returns, or the trend signal is corrupted.
2. **Cleaning**
   - Splits and dividends: use total return, not price.
   - Flag stale prices, missing days and outliers (|daily return| > 50% gets a manual check).
   - Keep the raw data and the cleaned data separately.
3. **Survivorship**
   - Build the universe point in time from dated index-membership snapshots, as B2/C0 already do.
   - **Open issue here:** the 2025-26 event sample uses S&P 500 members as of 2025-06-02. Names added after that date
     are missing, and names removed earlier are missing too. Rebuild it with monthly membership.
   - Keep delisted names and their delisting returns.
4. **Look-ahead**
   - Every datum carries a *knowledge time*, the moment it became public (SEC acceptance time, fixed at the close).
   - Signals are computed at t and traded at the t+1 open.
   - **LLM-specific:** Bonsai/Jan training data may postdate the test period, so find the models' training cutoffs.
     Rerun a sample with anonymized company names (Glasserman & Lin), and treat any period before the cutoff as in
     sample.
5. **Signals**
   - Use the published definitions, fixed before testing (for example, 12-1 month momentum and 12-month TSMOM).
   - Log every variant tried in a trials registry: its count feeds the Deflated Sharpe Ratio.
6. **Portfolio construction**
   - Rank → weights with position caps and a buy/hold band.
   - Sleeves: SPY core, crypto ≤ 20%, trend, factor tilt, event book.
7. **Position sizing** is risk-based (inverse volatility) with per-name and per-sleeve caps.
8. **Volatility targeting**
   - A 60-day exponentially weighted volatility estimate, targeting 10–12% for the whole book.
   - Scale capped at 1.5× so a volatility drop cannot force leverage.
9. **Leverage limits:** 1.0× gross until one full year of paper forward testing passes (item 20), and 1.5× after
   that at most.
10. **Transaction costs**
    - Half-spread + commission + impact ∝ σ·√(trade / ADV).
    - Test at 0, 10, 25 and 50 bps; a strategy must survive **2× the base assumption**.
11. **Slippage:** fill at the next open plus an adverse fraction of the open-to-close range. Check it against the
    paper-trading fills.
12. **Walk-forward:** expanding window with monthly refits, as the calibrator already does.
13. **Strict out-of-sample**
    - Hold back the last 3 years untouched, plus one other region or decade.
    - Open each once, after the rule is written in WEEK_PLAN.md.
14. **Monte Carlo**
    - Block bootstrap of monthly returns gives the distribution of CAGR, max drawdown and worst year.
    - Random-entry and shuffled-signal nulls, as with the p = 0.06 shuffle test already run.
15. **Parameter sensitivity:** plot the Sharpe over a grid of lookbacks and rebalance frequencies. Accept a plateau;
    reject a spike.
16. **Stress tests:** replay 1987, 2000–02, 2008, 2020 and 2022 (bonds and stocks falling together), plus synthetic
    shocks (a volatility tripling, correlations → 1).
17. **Probability of Backtest Overfitting:** combinatorially symmetric cross-validation over the full trial set.
    Reject if PBO > 0.3.
18. **Deflated Sharpe Ratio:** the number of trials comes from the registry. Require a DSR above 0.95.
19. **Paper trading**
    - At least 12 months in the simulator with orders generated automatically at the planned times.
    - Record implementation shortfall against the backtest's own fills.
20. **"Deployment" gate (paper only in this project)**
    - Forward IC or Sharpe within 1 standard error of the backtest.
    - Implementation shortfall below 30% of the gross edge.
    - No rule broken.
    - Any move to real money is a separate decision for the user; it is not part of this plan.

**Why forward tests cannot prove a Sharpe quickly.** The Sharpe's standard error is about √((1 + S²/2) / years).

| Target Sharpe | Years of track record for ~95% one-sided confidence |
|---|---|
| 1.0 | ≈ 3 |
| 0.5 | ≈ 11 |

The paper test therefore confirms the *implementation*; the *edge* has to come from long, multi-market historical
out-of-sample evidence.

## Part 6. Deliverables

### 6.1 Reading list (priority order)

1. Harvey, Liu, Zhu (2016)
2. Bailey & López de Prado (2014), Deflated Sharpe Ratio
3. Bailey, Borwein, López de Prado, Zhu (2017), Probability of Backtest Overfitting
4. McLean & Pontiff (2016)
5. Hou, Xue, Zhang (2020)
6. Jensen, Kelly, Pedersen (2023)
7. Moskowitz, Ooi, Pedersen (2012)
8. Hurst, Ooi, Pedersen (2012/2017)
9. Asness, Moskowitz, Pedersen (2013)
10. Koijen, Moskowitz, Pedersen, Vrugt (2018)
11. Daniel & Moskowitz (2016)
12. Barroso & Santa-Clara (2015)
13. Cederburg, O'Doherty, Wang, Yan (2020)
14. Novy-Marx & Velikov (2016)
15. Frazzini, Israel, Moskowitz (Trading Costs)
16. Gu, Kelly, Xiu (2020)
17. Avramov, Cheng, Metzker (2023)
18. Martineau (2022)
19. Chordia et al. (2009)
20. Lopez-Lira & Tang
21. Glasserman & Lin
22. Suhonen, Lennkh, Perez (2017)
23. Baltussen, Swinkels, van Vliet (2021)
24. Ehsani & Linnainmaa (2022)
25. Cornell (2020), Medallion

### 6.2–6.4 Directions, with the strongest evidence for and against

| Direction | Strongest for | Strongest against |
|---|---|---|
| Trend following (ETF or futures) | A century across many markets; net Sharpe 1.0 in 1903–2012 | 2003–12 net Sharpe 0.61; weak after the 2008 crisis; AQR's own forward assumption is 0.4 |
| Multi-asset carry | Global carry factor Sharpe 1.2 across 8 asset classes | Every carry strategy crashes together in recessions; needs futures or FX |
| Value + momentum + quality combination | Negative value–momentum correlation everywhere; 82% of factors replicate (JKP) | 58% decay after publication; 65% of anomalies fail HXZ; value's 2018–20 drawdown |
| Volatility-managed momentum | Sharpe 0.53 → 0.97; kurtosis 18 → 2.7 | Out-of-sample volatility management fails across 103 strategies (Cederburg et al.) |
| Factor momentum | 51 vs 6 bps a month; explains stock momentum (JF 2022) | Depends on the factor zoo; turnover |
| Earnings/LLM event book (this project) | 2025-26 IC +0.103 with a CI above 0; Lopez-Lira finds LLMs predict | 2024 IC +0.027 (not significant); PEAD dead in large caps; LLM look-ahead; very cost-sensitive |
| Crypto trend sleeve | Strong time-series momentum (RFS 2021) | One regime; 50–80% drawdowns |
| Volatility-targeting overlay | Cuts the tails of momentum | No reliable Sharpe gain out of sample |

### 6.5 Biggest methodological traps

The first four apply directly to this project.

1. **LLM training-data look-ahead.** A model whose training data covers the test period can "remember" the outcomes.
2. **Multiple testing across books, arms and features, with too few months.** Six books, several arms and 12 analyst
   features were tried on about 15 monthly points. Count every trial for the Deflated Sharpe Ratio.
3. **A short sample in a strong bull market.** The 2025-26 results cover about 2.5 years.
4. **Survivorship in a fixed-date universe.**
5. **Costs:** assuming institutional costs for a retail account, and ignoring turnover.
6. **Microcaps and equal weighting** inflating results.
7. **Futures roll gaps** in free continuous data.
8. **Volatility targeting** built with full-sample parameters.
9. **Choosing leverage from the backtest Sharpe**, which Part 1 shows is ruinous when the Sharpe halves.

### 6.6 Backtesting architecture

The engine is event-driven and runs on daily bars:

1. **Point-in-time store:** Parquet, keyed by (asset, knowledge_time).
2. **Signal modules:** pure functions of data known at time t.
3. **Portfolio constructor:** caps and a trading band.
4. **Cost/slippage model.**
5. **Fill simulator:** fills at t+1.
6. **Ledger.**

Around the engine:

- A **trials registry** (every run's config hash, counted for the Deflated Sharpe Ratio).
- **Pre-registration files** (WEEK_PLAN.md, as now).
- An **evaluation layer**: IC, Sharpe with confidence intervals, DSR, PBO, bootstrap, stress replays.

The project's `master_portfolio.py` / `horizons6_eval.py` already cover the ledger and the IC/bootstrap. The missing
pieces are the trials registry, DSR/PBO, and a monthly point-in-time universe.

### 6.7 Portfolio and risk architecture

1. **Risk budget by sleeve:**
   - SPY core
   - crypto ≤ 20%
   - trend sleeve
   - factor tilt
   - event book (capped until it passes)
2. **Book-level controls:**
   - 10–12% volatility target
   - 1.0× gross leverage
   - per-name cap 5%
3. **Pre-set drawdown rules** (for example, halve risk at −15% from peak and review at −25%). These are tested as
   rules in the backtest, not applied ad hoc.
4. **Correlation monitoring.**
5. **Kill switches:** data staleness, model server failure, or fills beyond the slippage limits.

### 6.8 Data

Everything above is free; nothing needs a subscription.

- Prices: Yahoo/Stooq.
- Fundamentals: SEC EDGAR/XBRL.
- Factors: French, AQR, JKP and Open Source Asset Pricing.
- Rates: FRED. Positioning: CFTC Commitments of Traders.
- Index membership: dated Wikipedia revisions, as now.
- Crypto: exchange public APIs.

### 6.9 What counts as convincing out-of-sample evidence

1. A pre-registered rule.
2. Untouched holdout periods **and** another market or decade.
3. A net Sharpe CI above 0 at 2× costs.
4. DSR > 0.95 and PBO < 0.3.
5. The effect holds after excluding the smallest 20% of names.
6. Positive in most sub-periods.
7. Twelve months of paper trading whose results sit within 1 standard error of the backtest.

### 6.10 Abandon a strategy when any of these holds

- Its net CI includes 0 at base costs.
- Its DSR is below 0.5 or its PBO is above 0.5.
- It works only in small or illiquid names or in one sub-period.
- Its sensitivity plot is a spike, not a plateau.
- Its paper results fall more than 2 standard errors below the backtest.
- Implementation shortfall exceeds half the gross edge.
- For LLM signals: anonymized-name runs lose the effect, or the effect exists only before the model's training cutoff.

Per the project rules, a failed strategy stays in the record as failed and is not silently dropped.

## User decisions (2026-09-26, 23:25)

1. **Instruments.** The paper simulator allows shorting, margin, futures and options. Long-short and futures-style
   sleeves are therefore in scope; the paper simulation charges financing and borrow costs.
2. **Target: 30–40% a year.** Drawdown tolerance "depends". The AI has to analyze the market, and after a sudden
   spike it analyzes *why* before responding.
3. **Scope.** A strategy family becomes a sleeve **if it improves the gain**. This is judged at equal risk (below),
   because raw gain can always be bought with leverage.

### What 30–40% requires

Same maths as Part 1: r = 4%, no financing spread or fees, so the numbers are optimistic.

| Target | Sharpe at 20% vol | at 25% | at 30% | Least possible Sharpe (full Kelly) |
|---|---|---|---|---|
| 30% | 1.21 | 1.01 | 0.89 | 0.67 |
| 35% | 1.40 | 1.17 | 1.02 | 0.72 |
| 40% | 1.58 | 1.31 | 1.14 | 0.77 |

Monte Carlo, 10 years, fat tails, with volatility set for a 35% CAGR:

| Backtest Sharpe, vol | True Sharpe | Median CAGR | Median max DD | 90th pct max DD |
|---|---|---|---|---|
| 1.4, 20% | 1.4 | 35% | 24% | 33% |
| | 0.98 | 24% | 28% | 39% |
| | 0.70 | 18% | 31% | 43% |
| 1.17, 25% | 1.17 | 35% | 32% | 43% |
| | 0.82 | 24% | 36% | 50% |
| | 0.58 | 17% | 40% | 56% |
| 1.0, 30% | 1.0 | 34% | 40% | 54% |
| | 0.70 | 23% | 45% | 61% |
| | 0.50 | 16% | 50% | 67% |

**Reading.** 30–40% is not ruled out the way 66% is. It needs a combined book with a *true, net* Sharpe of about
1.0–1.4 at 20–30% volatility, and it means living with peak-to-trough losses of roughly 25–45%. No single family in
Part 2 has a credible net Sharpe that high. The only route is to combine several roughly uncorrelated sleeves
(trend, carry, factor, the event book, crypto) and then apply moderate leverage. Each sleeve therefore earns its
place by raising the *combined* Sharpe. If the combined true Sharpe ends up nearer 0.7, the same risk gives about
17–23%.

### Rule for adding a sleeve (pre-registered, applies to every candidate)

- **Baseline:** B0 is the current master book (SPY core + crypto sleeve, `master_portfolio.py crypto`), 2018-01-02 to
  2026-09-24.
- **Candidate:** B1 is B0 plus the candidate sleeve as an overlay.
- **The sleeve is adopted only if both hold:**
  - B1's CAGR, after scaling B1 to B0's realized volatility, is higher than B0's CAGR.
  - The 90% block-bootstrap interval of Sharpe(B1) − Sharpe(B0) lies above 0 (3-month blocks, 2,000 resamples).
- **Separately, the sleeve must work on its own:** its net Sharpe over its post-publication window has a 95%
  block-bootstrap interval above 0.
- **Costs:** base 10 bps per unit traded; also reported at 25 bps.
- **Honesty:** one specification per sleeve, written below before its first run. A sleeve that fails stays in the
  record as failed.

### Sleeve 1: diversified trend following (spec written before any run)

- **Script:** `scripts/trend_sleeve.py`.
- **Instruments:** 20 liquid ETFs, standing in for futures because they have clean, split- and dividend-adjusted free
  Yahoo data:
  - Equities: SPY, QQQ, IWM, EFA, EEM, EWJ
  - Bonds: TLT, IEF, LQD, HYG, TIP
  - Commodities: GLD, SLV, USO, DBC, DBA
  - Currencies: UUP, FXE, FXY
  - Real estate: VNQ
  - Cash: BIL.
- **Signal** at each month-end close: the sign of the ETF's 252-day return minus BIL's 252-day return (Moskowitz,
  Ooi, Pedersen 12-month rule). Long if positive, short if negative.
- **Sizing:**
  - Raw weight = sign ÷ the ETF's 63-day volatility.
  - The book is scaled to 10% ex-ante volatility using the 252-day covariance, with a gross cap of 4×.
  - Weights set at close t earn returns from t+1 to the next rebalance.
- **Returns:**
  - Σ w·(r − r_BIL), minus 10 bps × Σ|Δw| at each rebalance.
  - Minus financing of 1.5% a year on long gross above 1×, and a 0.5% a year borrow fee on short gross.
- **Windows:** full 2008-01 → 2026-09; post-publication 2013-01 → 2026-09 (the paper appeared in 2012).
- **Pass rules:** as above. The standalone rule uses the 2013–26 window; the adding rule uses B0's 2018–26 window.

**Result (2026-09-26, 23:31): fails both rules.** Output: `results/trend_sleeve.json`, `trend_sleeve_25bps.json`.

- **Data note:** the first run silently lacked GLD and IWM because a Yahoo batch download dropped them. It is kept as
  `trend_sleeve_run1_missing_GLD_IWM.json`; its Sharpe was 0.43 over 2013–26, so the verdict is the same. The script
  now re-fetches missing tickers and refuses to run on a partial universe. The spec itself is unchanged.

**The sleeve alone** (turnover 11× a year, average gross 2.4×):

| Cost | Window | Excess CAGR | Vol | Sharpe | 95% CI | Max DD | Worst year |
|---|---|---|---|---|---|---|---|
| 10 bps | 2013–26 | 4.1% | 11.0% | 0.42 | −0.10 to 0.96 | 24.0% | −14.7% (2016) |
| 25 bps | 2013–26 | — | — | 0.25 | −0.28 to 0.78 | — | — |

- Best years: 2013 and 2022 (+23.4% each). Worst years: 2016 (−14.7%), 2018 (−13.3%), 2023 (−8.7%).
- This matches the literature: trend has been weak since 2009, and 0.4 is the forward Sharpe AQR itself assumes.

**Added to the book, 2018–26:**

| Book | CAGR | Vol | Sharpe | Max DD |
|---|---|---|---|---|
| B0 (current master book) | 21.6% | 20.6% | 1.05 | 33.8% |
| B1 (B0 + trend overlay) | 23.8% | 24.9% | 0.98 | 39.6% |
| B1 scaled to B0's volatility | 19.9% | 20.6% | 0.98 | — |

- Correlation between the trend sleeve and B0: 0.14.
- Sharpe(B1) − Sharpe(B0) = −0.07, 90% CI −0.34 to +0.20.
- **Verdict:** trend adds return only by adding risk; at equal risk it lowers the gain. It is **not adopted**. It stays
  in the record and is a candidate for re-testing only as part of a combined multi-sleeve book (next plan), under a
  new pre-registered rule.

### Next sleeves to test (specs to be written before each run)

1. **Multi-asset carry**, through futures in the simulator.
2. **Value + momentum + quality stock factor book**, long-short, on the point-in-time universe with monthly
   membership.
3. **Volatility-managed momentum.**
4. **The spike check** (below).

Each goes through the same adding rule. The combined book's leverage is chosen for 20–25% volatility only after at
least two sleeves pass.

### Algorithms added 2026-09-26, 23:38 (specs written before any run)

**A1. "Bonsai-lite": a matrix model that copies Bonsai and triages releases** (`scripts/bonsai_lite.py`)

- **Features:** one row per release, all known at the decision time, built as one numpy matrix:
  - EPS change vs a year earlier, as (q − prior) ÷ |prior|, clipped to ±2
  - revenue growth, clipped to ±1
  - a missing-value flag for each of those two
  - guidance: raised / lowered / maintained / none, one-hot
  - tone: positive / negative / neutral, one-hot
  - 12-1 month momentum vs the sector ETF, clipped to ±1
  - sector one-hot.

  The announcement-day reaction is left out, because it may postdate the decision.
- **Model:** ridge regression in closed form, (XᵀX + λI)⁻¹Xᵀy with λ = 1 on standardized features. The target is
  Bonsai's 1-week-book log-odds.
- **Walk-forward:** for each month of the 2025-26 sample, fit on all of 2024 plus the earlier 2025-26 months. No
  Bonsai output from the month being predicted is ever used.
- **Arms,** all on the same 2025-26 releases and scored as monthly rank IC on the 5-day excess return:
  - (a) Bonsai.
  - (b) Lite alone.
  - (c) **Triage:** Bonsai is called only for releases whose lite score is in the month's top half, which halves the
    GPU work. Every other release ranks below all called releases, ordered among themselves by lite score.
- **Pass rules:**
  - Lite *replaces* Bonsai for the 1-week book if IC(b) ≥ IC(a) − 0.01 **and** CI(b)'s lower bound is > 0.
  - Triage is adopted if IC(c) ≥ IC(a) − 0.01 **and** CI(c)'s lower bound is > 0.
  - Also reported: the rank correlation between lite and Bonsai (how well it copies).

**A2. Volatility-managed stock momentum, long-short** (`scripts/momentum_sleeve.py`)

- **Universe:** each year's point-in-time top-100 S&P 500 names (`data/hist`, 2010–26). About 10 delisted names have
  no Yahoo data; this mild survivorship bias works in the sleeve's favor.
- **Signal:** at each month-end, the return from t−252 to t−21.
- **Positions:** long the top 20% and short the bottom 20%, equal-weighted within each side.
- **Scaling** (Barroso & Santa-Clara): the book is scaled by 12% ÷ the realized volatility of the unscaled
  long-short's daily returns over the prior 126 days, capped at 2×.
- **Costs:** 10 bps × turnover, plus a 0.5% a year borrow fee on shorts.
- **Rules:** as for Sleeve 1. Standalone Sharpe CI > 0 over **2016-01 → 2026-09** (the paper appeared in 2015), and
  the adding rule against B0 over 2018–26.

**A3. G10 currency carry** (`scripts/fx_carry_sleeve.py`)

- **Currencies:** AUD, CAD, CHF, EUR, GBP, JPY, NZD, NOK and SEK against USD.
- **Data:** Yahoo spot rates, and FRED/OECD 3-month interbank rates (IR3TIB01…M156N).
- **Signal:** at each month-end, each currency's rate minus the US rate, using the *previous* month's value because
  of publication lag.
- **Positions:** long the top 3 and short the bottom 3, equal weight.
- **Scaling:** to 10% ex-ante volatility using the 252-day covariance of daily returns, with a gross cap of 4×.
- **Returns:** daily spot return in USD plus the rate differential ÷ 252, minus 10 bps × turnover.
- **Rules:** as for Sleeve 1. Standalone Sharpe CI > 0 over **2012-01 → 2026-09** (after Lustig, Roussanov and
  Verdelhan 2011, and Menkhoff et al. 2012), and the adding rule against B0 over 2018–26.

### Results of A1–A3 (2026-09-26, 23:41): all three fail their pre-registered rules

**A1 Bonsai-lite** (`results/events/bonsai_lite.json`)

- Inputs: 1,180 releases, 26 features.
- The ridge copy ranks releases with a **0.754 rank correlation to Bonsai**. It fits and predicts in milliseconds on
  the CPU.

| Arm | IC | 95% CI |
|---|---|---|
| (a) Bonsai | +0.103 | +0.051 to +0.158 |
| (b) Lite alone | +0.086 | +0.004 to +0.173 |
| (c) Triage (49% of Bonsai calls saved) | +0.083 | +0.011 to +0.158 |

- Both (b) and (c) have a CI above 0, but both miss the "within 0.01 of Bonsai" margin, so **both FAIL**. Bonsai
  stays the decider for the 1-week book.
- **Lead, not adopted:** lite keeps most of the signal with no GPU. That makes it a candidate fallback for the
  forward test on days the PC or the model server is off. That use needs its own pre-registered rule.

**A2 Volatility-managed momentum** (`results/momentum_sleeve.json`)

- Turnover 8.7× a year, average gross 1.4×.

| Window | CAGR | Vol | Sharpe | 95% CI | Max DD | Worst year |
|---|---|---|---|---|---|---|
| 2016–26 | 0.7% | 12.9% | 0.12 | −0.49 to 0.70 | 31.5% | −23.2% (2016) |

- Added to the book: correlation −0.01, but at equal risk the CAGR is 21.0% vs B0's 21.6%. Sharpe difference −0.02,
  90% CI −0.37 to +0.29. **FAIL on both rules.**
- In 100 mega-caps, momentum has been weak since 2016, consistent with its post-publication decay.

**A3 G10 FX carry** (`results/fx_carry_sleeve.json`)

- Turnover 2.6× a year, average gross 2.5×.
- The EUR and GBP OECD rates stop at 2026-01 and are carried forward from there.

| Window | CAGR | Vol | Sharpe | 95% CI | Max DD |
|---|---|---|---|---|---|
| 2012–26 | 1.1% | 13.1% | 0.15 | −0.16 to 0.59 | 31.7% |

- 2008: −19.8%, the carry crash.
- Added to the book: at equal risk the CAGR is 18.6% vs B0's 22.1%. Sharpe difference −0.14, 90% CI −0.32 to +0.13.
  **FAIL on both rules.**

**What the four sleeve tests say together** (trend, momentum, FX carry, plus Bonsai-lite for the event book):

- The published "free lunch" families, run honestly, after costs and after publication, add nothing to a book that
  is already mostly SPY + crypto in a strong 2018–26 bull market.
- That matches the decay literature (McLean & Pontiff; the backtest-to-live haircut of Suhonen et al.).
- For the 30–40% target, the only candidates left with a CI above 0 in this project are the Bonsai event book
  (1-week) and the SPY + crypto core. Neither has shown a true Sharpe above 1 over a long enough sample.
- **Next honest step:** keep the forward test running to accumulate out-of-sample months; do not lever up on
  2025-26 alone.
- **Sleeves still untested:** commodity/bond carry needs futures curve data that isn't freely available;
  value + quality needs point-in-time fundamentals history (EDGAR XBRL, 2009+). Each gets its own spec before its run.

### Spike check: analyze before responding (design, to be built and tested next)

- **Trigger (checked once a day at the close):**
  - A holding's |daily return| is above 4× its 60-day daily volatility, or
  - SPY or BTC is above 3× its own.
- **Response:**
  1. Automatic *discretionary* changes to that asset are frozen for the day.
  2. Jan gathers as-of news and filings for it.
  3. Bonsai writes a source-checked "cause brief" and labels the cause: earnings, company news, sector,
     macro/market-wide, or unexplained.
  4. The master agent then applies a fixed rule per label. Examples:
     - An unexplained spike halves the position until explained.
     - A macro spike defers to the book's volatility target.
- **Hard risk limits never wait for the analysis.** These include the volatility target, drawdown brakes and position
  caps.
- **Test:**
  - Arm A: the mechanical response. Arm B: analyze first.
  - Both run on every historical trigger in 2024–26.
  - B is adopted if its 5-day forward return on the triggered positions beats A's, with a 95% interval above 0.
- **Cost:** about 3 s for Jan plus about 9 s for Bonsai per trigger. Triggers are a handful per week.
