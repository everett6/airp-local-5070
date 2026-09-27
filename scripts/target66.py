"""Sharpe/volatility/leverage needed for ~66%/yr, and a fat-tailed Monte Carlo of the drawdowns (docs/STRATEGY_RESEARCH.md, Part 1)."""
import numpy as np
g = np.log(1.66); rf = 0.04; spread = 0.015   # retail margin ~ rf + 1.5% on borrowed money
print(f"log growth needed {g:.3f}/yr")
print("Required Sharpe (excess, after costs) by portfolio vol, no financing spread, rf=4%:")
for s in [.10,.15,.20,.25,.30,.40,.50,.60,.80,1.0]:
    S=(g-rf+s*s/2)/s; print(f"  vol {s:4.0%}: Sharpe {S:4.2f}")
print("Minimum (Kelly) Sharpe:", round(np.sqrt(2*(g-rf)),2), "at vol = Sharpe")
# leverage of a base strategy with own vol 10%, 15%
print("\nLeverage needed on a strategy with unlevered vol v and Sharpe S (financing rf+1.5% on borrowed):")
for v in [.10,.15]:
  for S in [.5,.8,1.0,1.2,1.5]:
    best=None
    for L in np.arange(1,30,0.01):
        mu = rf + L*S*v - (L-1)*spread  # arithmetic
        gg = np.log1p(mu) - (L*v)**2/2 if False else mu - (L*v)**2/2
        if gg>=g: best=L;break
    print(f"  v={v:.0%} S={S}: L={best if best is None else round(best,1)}", "" if best is None else f"-> vol {best*v:.0%}")
# Monte Carlo: 10 years daily, Student-t(4) scaled, for combos reaching ~66% CAGR in expectation
rng=np.random.default_rng(0)
def sim(S,sig,years=10,n=4000,df=4, trueS=None):
    trueS = S if trueS is None else trueS
    d=252*years; mu=(rf+trueS*sig)/252; sd=sig/np.sqrt(252)
    z=rng.standard_t(df,(n,d))/np.sqrt(df/(df-2))
    r=np.maximum(mu+sd*z,-0.99)
    lw=np.cumsum(np.log1p(r),axis=1)
    peak=np.maximum.accumulate(lw,axis=1); dd=1-np.exp(lw-peak)
    mdd=dd.max(1); cagr=np.exp(lw[:,-1]/years)-1
    ann=np.exp(lw[:, 251::252])  # yearly
    yr=np.diff(np.concatenate([np.ones((n,1)),ann],1),axis=1)/np.concatenate([np.ones((n,1)),ann[:,:-1]],1)
    return np.median(cagr), np.percentile(mdd,[50,90]), np.median(yr.min(1)), (cagr<0).mean(), (mdd>0.8).mean()
print("\nMonte Carlo, 10 yrs, fat tails (t4). Vol chosen so median CAGR ~66% if backtest Sharpe is TRUE:")
for S,sig in [(3.2,.15),(2.4,.20),(1.7,.30),(1.2,.50),(1.0,.80)]:
    for trueS in [S, S/2]:
        c,m,w,p0,p80=sim(S,sig,trueS=trueS)
        print(f"  backtest S={S} vol {sig:.0%} true S={trueS:.2f}: median CAGR {c:6.1%}  MDD median {m[0]:.0%} p90 {m[1]:.0%}  median worst yr {w:6.1%}  P(10y loss) {p0:.0%}  P(DD>80%) {p80:.0%}")
