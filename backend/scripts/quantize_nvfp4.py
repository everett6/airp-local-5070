"""Make an NVFP4 (4-bit floating point, Blackwell) copy of Jan-v1-4B for vLLM, calibrated on our own press releases.

    ~/llmc-env/bin/python scripts/quantize_nvfp4.py            # ~/models/Jan-v1-4B -> ~/models/Jan-v1-4B-NVFP4
    QUANT=nvfp4 scripts/vllm_serve.sh                          # serve it

llm-compressor's NVFP4 scheme (weights and activations in FP4 with FP8 block scales, lm_head kept in BF16) needs a
short calibration pass to set the activation scales: 256 chunks of 2,048 tokens from the earnings releases already in
data/events/text (the text the research agent actually reads), so nothing extra is downloaded. Run it in its own
environment (~/llmc-env) so llm-compressor's pinned packages can't break the vLLM install.
"""
from __future__ import annotations

import argparse
import gzip
import random
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=str(Path.home() / "models" / "Jan-v1-4B"))
    ap.add_argument("--out", default=str(Path.home() / "models" / "Jan-v1-4B-NVFP4"))
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--seq-len", type=int, default=2048)
    ap.add_argument("--device", default="cuda", help="cuda (needs ~9 GB free) or cpu (slow)")
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from llmcompressor import oneshot
    from llmcompressor.modifiers.quantization import QuantizationModifier
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16,
                                                 device_map=args.device)
    files = sorted((BACKEND / "data" / "events" / "text").glob("*.txt.gz"))
    random.Random(0).shuffle(files)
    texts = [gzip.decompress(f.read_bytes()).decode(errors="replace")[:12000] for f in files[: args.samples]]
    ds = Dataset.from_dict({"text": texts}).map(
        lambda b: tok(b["text"], truncation=True, max_length=args.seq_len, add_special_tokens=False),
        batched=True, remove_columns=["text"])
    oneshot(model=model, dataset=ds, recipe=QuantizationModifier(targets="Linear", scheme="NVFP4",
                                                                  ignore=["lm_head"]),
            max_seq_length=args.seq_len, num_calibration_samples=args.samples)
    model.save_pretrained(args.out, save_compressed=True)
    tok.save_pretrained(args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
