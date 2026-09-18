"""Bench DG captcha OCR: fetch N live captchas, run solve_captcha, save labeled set.

Usage:
    python3 scripts/dg_captcha_bench.py --n 12 --out data/dg_captcha_bench

Writes {out}/{i}.jpg + {out}/guesses.json {file, ocr_guess, ok|error}.
Ground-truth labeling is done by a human reading the images afterwards.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from tgpc.details_dg import DgFetcher, FORM_PATH, CAPTCHA_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--out", default="data/dg_captcha_bench")
    parser.add_argument("--min-delay", type=float, default=2.0)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fetcher = DgFetcher(min_delay=args.min_delay)
    base = fetcher.config.base_url
    guesses = []
    for i in range(args.n):
        try:
            fetcher._get(f"{base}{FORM_PATH}")
            cap = fetcher._get(f"{base}{CAPTCHA_PATH}", headers={"Referer": f"{base}{FORM_PATH}"})
            img_path = out / f"{i:02d}.jpg"
            img_path.write_bytes(cap.content)
            try:
                from tgpc.details_dg import solve_captcha

                guess = solve_captcha(cap.content)
            except Exception as e:  # noqa: BLE001
                guess = f"ERROR: {e}"
            guesses.append({"file": img_path.name, "ocr_guess": guess})
            print(f"{img_path.name}: {guess}", flush=True)
        except Exception as e:  # noqa: BLE001
            guesses.append({"file": None, "ocr_guess": f"FETCH_ERROR: {e}"})
            print(f"fetch {i} failed: {e}", flush=True)
    (out / "guesses.json").write_text(json.dumps(guesses, indent=2))
    print(f"saved {len(guesses)} to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
