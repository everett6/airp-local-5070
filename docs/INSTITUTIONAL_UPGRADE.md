# Institutional engineering upgrade — 3 October 2026

Scope authorized by the user: finish six engineering areas, paper only. No mandate or strategy recipe changes,
no registered strategy trials, model downloads, new timers, real orders during verification, or commits by Codex.

1. Extraction: source-bound human review workflow, independent sign-off status, reproducible reader scores.
2. Shared risk: enforce account limits including pending orders, quotes, buying power, concentration and daily loss;
   preserve duplicate recovery and risk-reducing exits. Log every decision before submitting.
3. Execution: arrival bid/ask, quote age, submission latency, cumulative fills, fees/financing inputs and cost stress.
4. Attribution: explicit market/crypto/sector factors, exposure alignment, uncertainty and insufficient-data states.
5. Releases: structured step records, complete local checks, immutable source manifests, independent approvals,
   reproducible bundles and verified rollback preparation without changing production automatically.
6. Resilience: verified backups/restores, safe scratch rehearsals for outages, load/deadlines and broker failures.

The benchmark's existing labels were produced by an AI. Software can make review traceable; it cannot provide
independent human verification. The app must continue to show this prerequisite until a reviewer checks sources.

Risk defaults are operating controls, not a fitted trading rule: maximum account gross 2x equity, asset absolute
exposure 1.8x, crypto gross 45%, daily account loss 5%, spread 100 bp, reference/arrival gap 10%, quote age 180 s
during trading (crypto always). Queued stock orders use the latest completed exchange session's closing quote
within 15 minutes of its close; the broker calendar supplies holidays and early closes. Risk-reducing exits can
proceed despite exposure/daily-loss breaches when they introduce no additional exposure. Policy lives separately
from the unchanged strategy mandate. Reports remain descriptive and never claim a validated alpha from a short sample.

Implemented controls and app workflows:

- **Source review** shows the original cached text beside editable benchmark labels. A named reviewer must
  verify period, units, basis and absence and type VERIFIED. Corrections are append-only and bound to the
  source and original-label digests. `extraction_benchmark.py --human-verified` refuses qualification while
  any case lacks a current approved review. No benchmark case has been approved by the coding agent.
- **Shared submission gate** runs inside `Alpaca.submit()`, with a process lock, before a paper POST. It includes
  remaining quantities of outstanding orders without netting independent buys and sells. Recovering an
  existing client ID happens first, preserving crash recovery. The audit is written before submission.
- **Execution records** retain arrival bid/ask, quote time/feed, latency and the latest cumulative fill snapshot.
  Cost stress adds 5, 10 or 25 bp per side to measured turnover. The app accepts fees, borrow and financing
  dollars supplied by a person with a source note; later ledger changes invalidate those inputs. IEX arrival
  quotes are explicitly labelled, and impact remains a stress assumption.
- **Factor report** uses local SPY, BTC, ETH, QQQ, TLT and sector closes on matching dates without filling gaps.
  It needs at least 120 matched daily observations and reports an intercept interval using Newey-West
  covariance. Missing data and collinear factors have explicit states; the short forward sample has no alpha
  qualification. Historical simulator returns and actual broker execution costs remain distinct measures.
- **Checked releases** preserve code, configuration, tests and dependency locks in a hashed archive after
  source-bound backend tests, safety lint/types, app syntax checks and app tests pass. Reviews in the app are
  attached to an archive digest. A named local attestation is not proof of reviewer independence. Neither
  packaging nor review activates a new strategy. CI now also covers the desktop and long-history branch.
- **Recovery** archives forward records, excludes environment files and symlinks, verifies the manifest and
  archive digest, and restores into a scratch folder without replacing production. Release rollback uses the
  same verification path. Copy the portable archive and manifest to separate storage for recovery from loss
  of this PC; a local copy alone does not cover that failure.
- **Structured outcomes** are emitted by the main decision, allocator and paper-order steps. Scheduled and
  manual runners preserve structured stage records and output digests; a declared failure overrides exit zero.
  Remaining older scripts are visibly marked as legacy/unknown rather than inferred healthy from exit zero.

Verification uses synthetic exchange transports, scratch folders and cached sources. The recovery action now
includes account-gate failures, delayed 80-filing throughput, GPU unavailability, partial fills, lost responses,
rejected hedges and missed runs. Human source verification, independent release review, more forward data and
placing a backup on separate storage remain evidence requirements, not operations an AI can truthfully sign off.

The app's **Engineering checks** page exposes these records and actions. **Run Center** saves their logs and
results across app restarts. Manual workers share the scheduled-job lock and yield time to automatic runs.

### Reliability and forward-measurement completion — 3 October 2026

AI sleeve orders now carry the latest completed close as their reference, fixing the zero-reference rejection at the account gate without bypassing quotes or limits. Missing references remain rejected. Broker validation errors retain safe local diagnostic text. No manual paper sync or scheduled rerun was performed during implementation; pending orders use the fix on the next authorized run.

Prospective sleeve steps preserve before/after simulator state, mode, decisions and exact open/close inputs in a hash-chained replay journal. The legacy replay is still reported honestly as unmatched; its incremental comparisons are suppressed and discrepancies are listed. The new journal reproduces its scope exactly or refuses measurement; counterfactuals inherit common legacy starting holdings. Historical missing bars cannot be reconstructed retroactively.

Fresh research revision 2 persists tool logs, model replies, source evidence/hashes, brief verification drops and stage-specific failure reasons even on failures. A maximum of one brief-format repair uses the same evidence within the original 180-second timeout. Old failed cohorts remain failed; no real-model cohort was rerun during implementation.

Source review now gives candidate numeric excerpts beside the full source, explicitly without verifying the period, units or basis. Engineering evidence lists separate reader-file coverage and provisional/human-reviewed scores, including Jan where records exist. No person has signed off these labels; sources and human checks remain necessary.

Execution reporting includes legacy filled legs as missing arrival coverage, distinguishing unavailable costs from zero cost. Human cost reviews bind to both the execution ledger and known filled legs, so changes invalidate stale evidence. No missing fee, borrow or financing value is invented.

HS1 is specified in PLAN_60_V2 before code. New LRF cohorts lock day/medium/long targets and a matched no-AI arm. The Horizon shadows page and read-only-market refresh action evaluate post-decision free IEX daily price proxies at 1/21/63 session horizons with 10/20 bp per side, identical price coverage and no pooling or automatic promotion. Existing cohorts are excluded. No registered trial, mandate change, real trade, paid data, new timer or model download is involved.

Validation: 793 synthetic backend tests and 23 app tests passed; all changed Python modules passed Ruff and strict mypy. Every app page rendered without an error in the offscreen smoke test. A read-only Alpaca gate probe showed that the zero-reference error is resolved, but current closed-market quotes still fail spread/quote checks. Local gate blocks now reuse client IDs, do not add duplicate leg records, and do not consume the broker exit retry allowance. No paper orders, registered trials or real-model cohort were executed during validation. Human benchmark attestation, external broker cost evidence and future matured horizon observations remain prerequisites.
