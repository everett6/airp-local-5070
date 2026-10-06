"""App review API: sources and original labels, or an explicit authenticated human attestation."""
import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.forward.benchmark_review import attest, materials


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--attest", default="")
    args = ap.parse_args()
    if args.attest:
        data = json.loads(args.attest)
        if data.pop("confirm", None) != "VERIFIED":
            raise ValueError("Type VERIFIED to record your review")
        attest(BACKEND, **data)
    print(json.dumps(materials(BACKEND)))


if __name__ == "__main__":
    main()
