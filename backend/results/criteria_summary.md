#### `v5_fund_top100` — 5-day horizon, 64 cutoffs, scored from 2025-08-27

| arm | rank IC [95% CI] | top−bottom fifth %/period | IC gap vs sue_rule [CI] | Brier gap vs always-up [CI] |
|---|---|---|---|---|
| llm_fund (primary) | -0.0229 [-0.0745, +0.0286] | -0.359 | -0.0381 [-0.0962, +0.0147] | +0.0011 [+0.0001, +0.0022] |
| llm_plain | -0.0194 [-0.0747, +0.0330] | -0.462 | -0.0346 [-0.1009, +0.0241] | +0.0010 [-0.0001, +0.0021] |
| llm_selfimprove | +0.0076 [-0.0493, +0.0637] | +0.378 | -0.0076 [-0.0617, +0.0454] | +0.0023 [+0.0007, +0.0040] |
| feat_fund_logit | -0.0145 [-0.0571, +0.0292] | -0.004 | -0.0297 [-0.0725, +0.0100] | +0.0104 [+0.0036, +0.0182] |
| rl_forecast | -0.0212 [-0.0755, +0.0309] | -0.129 | -0.0162 [-0.0693, +0.0373] | +0.0045 [+0.0002, +0.0088] |
| sue_rule | +0.0152 [-0.0138, +0.0451] | +0.107 | — | +0.0001 [-0.0004, +0.0006] |

Phase C criteria (primary arm): rank IC CI > 0: **False**; beats surprise baseline: **False**; leak probe 0.01 < 20%: **True** → **NOT PASSED**
Phase R: weeks trained 60 {'rejected_forecast': 13, 'rejected': 24, 'rejected_trader': 3, 'not_enough_data': 4, 'adopted': 20}; rl_forecast beats always-up on Brier: **False**; trader after-cost Sharpe -0.092 [CI -2.06, 2.056], mean weekly -0.0024% after costs (0.0021% gross), mean |position| 0.0887: **False** → **NOT PASSED**
Deflated Sharpe (79 trials across all published runs): best long/short arm `sue_rule` DSR 0.733 (PSR 0.990); luck benchmark 1.507 annualized Sharpe

#### `v6_fund_rank20d` — 20-day horizon, 16 cutoffs, scored from 2025-09-25

| arm | rank IC [95% CI] | top−bottom fifth %/period | IC gap vs sue_rule [CI] | Brier gap vs always-up [CI] |
|---|---|---|---|---|
| llm_fund (primary) | -0.0248 [-0.0959, +0.0425] | -0.504 | -0.0566 [-0.1551, +0.0358] | +0.0023 [-0.0006, +0.0054] |
| llm_plain | -0.0073 [-0.0896, +0.0736] | +0.496 | -0.0391 [-0.1597, +0.0733] | +0.0020 [-0.0014, +0.0056] |
| llm_selfimprove | -0.0272 [-0.1262, +0.0677] | -0.388 | -0.0591 [-0.1927, +0.0697] | +0.0059 [-0.0042, +0.0182] |
| feat_fund_logit | +0.0568 [-0.0245, +0.1462] | +2.660 | +0.0250 [-0.1000, +0.1549] | +0.0225 [+0.0071, +0.0381] |
| rl_forecast | +0.0652 [+0.0185, +0.1175] | +3.008 | +0.0373 [-0.0386, +0.1297] | +0.0064 [-0.0099, +0.0218] |
| sue_rule | +0.0318 [-0.0395, +0.1021] | +1.216 | — | +0.0004 [-0.0006, +0.0013] |

Phase C criteria (primary arm): rank IC CI > 0: **False**; beats surprise baseline: **False**; leak probe 0.01 < 20%: **True** → **NOT PASSED**
Phase R: weeks trained 12 {'rejected': 1, 'not_enough_data': 4, 'adopted': 10, 'rejected_forecast': 1}; rl_forecast beats always-up on Brier: **False**; trader after-cost Sharpe 1.531 [CI -0.655, 3.06], mean weekly 0.517% after costs (0.5293% gross), mean |position| 0.2448: **False** → **NOT PASSED**
Deflated Sharpe (79 trials across all published runs): best long/short arm `sue_rule` DSR 0.530 (PSR 0.966); luck benchmark 1.953 annualized Sharpe

#### `v7_phase_f` — 5-day horizon, 64 cutoffs, scored from 2025-08-27

| arm | rank IC [95% CI] | top−bottom fifth %/period | IC gap vs sue_rule [CI] | Brier gap vs always-up [CI] |
|---|---|---|---|---|
| llm_fund (primary) | -0.0229 [-0.0745, +0.0286] | -0.359 | -0.0381 [-0.0962, +0.0147] | +0.0011 [+0.0001, +0.0022] |
| llm_plain | -0.0194 [-0.0747, +0.0330] | -0.462 | -0.0346 [-0.1009, +0.0241] | +0.0010 [-0.0001, +0.0021] |
| llm_selfimprove | +0.0076 [-0.0493, +0.0638] | +0.378 | -0.0076 [-0.0617, +0.0455] | +0.0023 [+0.0007, +0.0040] |
| feat_fund_logit | -0.0145 [-0.0571, +0.0292] | -0.004 | -0.0297 [-0.0725, +0.0100] | +0.0104 [+0.0036, +0.0182] |
| rl_forecast | -0.0299 [-0.0839, +0.0232] | -0.178 | -0.0239 [-0.0742, +0.0354] | +0.0033 [-0.0007, +0.0072] |
| sue_rule | +0.0152 [-0.0138, +0.0451] | +0.107 | — | +0.0001 [-0.0004, +0.0006] |
| llm_fund_lp | -0.0040 [-0.0577, +0.0510] | -0.033 | -0.0192 [-0.0765, +0.0352] | +0.1987 [+0.1765, +0.2214] |
| llm_lp | -0.0040 [-0.0587, +0.0507] | -0.022 | -0.0193 [-0.0834, +0.0386] | +0.1864 [+0.1619, +0.2133] |
| anomaly_rank | +0.0078 [-0.0474, +0.0627] | -0.155 | -0.0074 [-0.0550, +0.0421] | +0.0005 [-0.0006, +0.0015] |
| anomaly_logit | -0.0164 [-0.0656, +0.0309] | -0.293 | -0.0317 [-0.0785, +0.0128] | +0.0095 [+0.0028, +0.0171] |
| kronos | -0.0183 [-0.0746, +0.0397] | -0.205 | -0.0336 [-0.1041, +0.0371] | +0.0362 [+0.0280, +0.0440] |

Phase C criteria (primary arm): rank IC CI > 0: **False**; beats surprise baseline: **False**; leak probe 0.01 < 20%: **True** → **NOT PASSED**
Phase R: weeks trained 60 {'rejected_forecast': 15, 'rejected': 24, 'rejected_trader': 6, 'not_enough_data': 4, 'adopted': 15}; rl_forecast beats always-up on Brier: **False**; trader after-cost Sharpe -0.367 [CI -2.064, 1.99], mean weekly -0.0079% after costs (-0.0038% gross), mean |position| 0.0811: **False** → **NOT PASSED**
Phase F (primary llm_fund_lp): F1 rank IC CI > 0: **False**; F2 beats baselines (anomaly_rank: -0.0118 [-0.0836, +0.0532] kronos: +0.0144 [-0.0919, +0.1175] sue_rule: -0.0192 [-0.0765, +0.0352]): **False**; F3 no leak (identification < 20% and LAP interaction d CI [-22244.7642515147, 34672.757673486114] not above 0; NOTE: LAP ~ 0 everywhere, so the interaction test is uninformative and the identification probe carries F3): **True** → **NOT PASSED**
Phase R (v7): rl_forecast beats always-up on Brier: **False**; trader Deflated Sharpe 0.005308107305586382 > 0.95: **False** → **NOT PASSED**
Deflated Sharpe (79 trials across all published runs): best long/short arm `sue_rule` DSR 0.493 (PSR 0.990); luck benchmark 2.071 annualized Sharpe
