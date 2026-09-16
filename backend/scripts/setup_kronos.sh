#!/usr/bin/env bash
# Kronos baseline setup: pinned code commit + separate venv with torch (kept out of the main install and CI).
set -euo pipefail
cd "$(dirname "$0")/.."
COMMIT=67b630e67f6a18c9e9be918d9b4337c960db1e9a
if [ ! -d third_party/Kronos ]; then
  mkdir -p third_party && git clone -q https://github.com/shiyu-coder/Kronos.git third_party/Kronos
fi
git -C third_party/Kronos checkout -q "$COMMIT"
[ -d .venv-kronos ] || python3 -m venv .venv-kronos
.venv-kronos/bin/pip install -q torch einops==0.8.1 "huggingface_hub>=0.33,<1" safetensors pandas numpy tqdm
.venv-kronos/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
