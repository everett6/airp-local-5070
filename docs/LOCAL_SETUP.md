# Local Setup — Two GPUs, Two PCs

This assumes what you described: an RTX 5070 in one PC and an RTX 3060 in
another, both on the same LAN. The backend runs on whichever machine you
like (even a third one, e.g. a laptop) and talks to both GPU boxes over
HTTP — nothing here requires the backend and a GPU to be on the same machine.

## 1. Install Ollama on both GPU machines

On **both** the 5070 PC and the 3060 PC:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # Linux
# or download the installer from https://ollama.com/download for Windows/Mac
```

## 2. Expose Ollama to your LAN (not just localhost)

By default Ollama only listens on `127.0.0.1`, which is invisible to other
machines on your network. On **both** machines, start it with:

```bash
# Linux/macOS
OLLAMA_HOST=0.0.0.0 ollama serve

# Windows (PowerShell)
$env:OLLAMA_HOST="0.0.0.0"; ollama serve
```

If you installed Ollama as a system service, set `OLLAMA_HOST=0.0.0.0` as an
environment variable for that service instead (systemd: `Environment=` line
in the unit's override; Windows: set it as a system environment variable and
restart the Ollama service) so it persists across reboots.

**Firewall**: allow inbound TCP on port `11434` on both machines. On Linux:
`sudo ufw allow 11434/tcp`. On Windows, allow it through Windows Defender
Firewall for your local network profile only — do not expose 11434 to the
public internet.

Find each machine's LAN IP (`ip addr` / `ipconfig`) — you'll need both.

## 3. Pull models sized for each card

Both cards are 12GB-class, but the 5070 is meaningfully faster — use it for
the model doing the harder reasoning work (bull/bear thesis, portfolio
synthesis), and use the 3060 for faster/lighter extraction work.

On the **5070** machine:
```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
```
A 14B model at Q4 quantization fits comfortably in 12GB and is meaningfully
better at weighing conflicting evidence than a 7-8B model — worth the extra
latency for the reasoning role.

On the **3060** machine:
```bash
ollama pull llama3.1:8b-instruct-q4_K_M
```
An 8B model here keeps extraction-style calls (summarizing one filing
section, writing a narrative paragraph from already-computed numbers) fast,
which matters more than depth for that role.

If your 3060 is the 8GB variant rather than 12GB, drop to a smaller model
(e.g. `llama3.2:3b-instruct-q4_K_M` or `phi3.5:3.8b-mini-instruct-q4_K_M`) —
check `nvidia-smi` while a model is loaded to confirm you're not swapping to
system RAM, which will tank throughput far worse than a smaller model would.

## 4. Point the backend at both machines

In `backend/.env` (copy from `.env.example`):

```
LLM_REASONING_BASE_URL=http://192.168.1.50:11434
LLM_REASONING_MODEL=qwen2.5:14b-instruct-q4_K_M

LLM_EXTRACTION_BASE_URL=http://192.168.1.51:11434
LLM_EXTRACTION_MODEL=llama3.1:8b-instruct-q4_K_M
```

Replace the IPs with your actual LAN addresses from step 2. Leave either
`_BASE_URL` blank and that role automatically falls back to a mock LLM
client — useful for confirming the rest of the app works before your GPUs
are configured.

## 5. Verify each endpoint independently before starting the app

```bash
curl http://192.168.1.50:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:14b-instruct-q4_K_M","messages":[{"role":"user","content":"say ok"}]}'
```

Do this from the machine that will run the backend, not from the GPU
machine itself — a response confirms both that Ollama is listening on the
LAN interface and that the firewall rule is actually working. Repeat for the
3060 endpoint.

## 6. Run the backend and frontend

```bash
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Or via Docker Compose (`docker-compose.yml` in the repo root) if you'd
rather not manage Python/Node versions yourself — the compose file does not
run Ollama itself (it can't reach GPUs across two separate physical
machines from inside a container), so steps 1-5 above are still required
regardless of whether you run the app itself in Docker.

## Why routing is configured this way, not auto-detected

The backend does not attempt to auto-discover Ollama instances on your LAN
or auto-select models based on detected VRAM. Two reasons:

1. **Explicitness.** Which model handles which agent role has real
   consequences for report quality (see `app/llm/router.py`'s docstring) —
   auto-detection would make that an opaque runtime decision instead of a
   reviewable config line.
2. **Failure mode.** If auto-discovery silently picked a different model
   than you expected (e.g. because your 3060 box was rebooting), you'd get
   a report that looks the same but was produced by a different model with
   no record of that — worse than the explicit "endpoint unreachable, this
   role fell back to the mock client" behavior you get today (visible in
   the report's confidence/data-quality scoring, since a mock LLM response
   degrades the data quality signal for anything depending on it).

## Single-GPU or single-machine fallback

Only have one GPU, or want to run everything on one box? Point both
`LLM_REASONING_BASE_URL` and `LLM_EXTRACTION_BASE_URL` at the same Ollama
instance with two different models (or even the same model for both roles)
— the router doesn't require the two endpoints to be different machines,
just different named configs. Performance will be lower since reasoning and
extraction calls now queue behind each other on one card instead of running
in parallel across two.
