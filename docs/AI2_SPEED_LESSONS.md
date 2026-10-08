# AI2 speed lessons applied to airp (7 Oct 2026)

AI2 is the local-model stack in ~/AI2, the "LM Studio model paths" chat. It took a 30B model from 47 to 188 tok/s
on this same RTX 5070. Each AI2 change was checked against Night's research. The numbers below come from the last
30 deep-research runs (results/forward/deep_research), using the llama-server timing lines in jan.log and bonsai.log.

**Where a company's time goes (medians):** Jan research takes 60 s, Bonsai judging 119 s (of which 37 s is web
lookups), and server start 3 s. Most model time is spent writing replies, not reading prompts. Bonsai read 6.07M
prompt tokens in 4,822 s, against 14,481 s of request time in total. Jan read 2.6M tokens in 427 s, against 7,375 s.

| AI2 change | airp status |
|---|---|
| `-b/-ub 1024` (+32% prompt reading) | Already in place. Ollama starts both models with `-b 1024 -ub 1024`. |
| Flash attention | Already on (`--flash-attn on`). |
| Fit layers to free VRAM | Already done: Jan 37/37 layers and Bonsai 65/65 layers are on the GPU. |
| KV cache quantization (AI2 measured −10 tok/s) | Ollama uses f16 KV, which is the faster option. The label client (`llamacpp_client.py`) uses q8_0. It is left as is because it feeds registered B4b labels. |
| Prompt (prefix) cache | Already working. Bonsai restores checkpoints, for example 3,933 of 7,186 tokens reused. |
| 175 W power cap (the Xid 79 crashes) | Already applied at boot by `ai2-gpu-power-cap.service`. This is the same card. |
| Server stdout pipe never read (AI2's freeze) | Checked: every airp child process writes to a file. |
| Over-long prompt reported as a server failure | Already handled: `ask()` records `overflow`. Two replies in the last 30 runs hit the context limit, both Jan. |
| A half-broken GPU makes everything fail confusingly | **Applied** (`full_auto.gpu_problem`). Night checks `nvidia-smi` before each batch. If it fails, research waits, the app shows why, and no company is charged a failed attempt. Before this, two GPU failures rested a company for 24 h. This happened tonight: the 21:14 `apt upgrade` left the 610 kernel module running with 615 libraries. |
| Run two models at once (pipelining) | Rejected: Jan needs 6.0 GB (2.4 GB model and 3.5 GB KV for 3 slots) and Bonsai 4.75 GB, which is too much for 12 GB with the desktop running. |
| n-gram speculative decoding | Not applied. AI2 found it does not produce identical replies. Ollama doesn't offer it, and Bonsai is a hybrid model (KV shifting is off for it), so support is uncertain. It would need its own measured trial. |
| Thread pinning, CPU threads | Not relevant: no layers run on the CPU. |

**Applied 7 Oct (late):** two companies are now judged at once (`app/sandbox/judge_lane.py`, `AIRP_JUDGE_OVERLAP=2`;
set 1 to go back). One Bonsai lane serves one request at a time, so model calls never run together, and a call's
timeout pauses while it waits for the lane, so queueing cannot cause a timeout. In the synthetic test, 4 companies with
lookups finish more than 1.3x faster. The real gain comes from Night's `loads.judge_lane_wait_s` and `judge_s`;
the estimate before measuring is 1.2–1.4x on the judge stage.
First live batch (8 Oct 04:39 UTC, 3 companies judged): Bonsai was busy 326 s of the 329 s judge stage, against
about 438 s run one after another (109 s of model time plus about 37 s of lookups per company), so **about 1.33x**.
Lane wait was 158 s, no call timed out, and all 3 were decided. That is one batch, so check it again over a night.
