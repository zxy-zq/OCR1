from __future__ import annotations

import argparse
import json
from pathlib import Path

from rapidocr import RapidOCR


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / ".models" / "rapidocr"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RapidOCR on a local image.")
    parser.add_argument(
        "image",
        nargs="?",
        default="rapidocr_test.png",
        help="Image path. Defaults to rapidocr_test.png in this folder.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full OCR result as JSON.",
    )
    parser.add_argument(
        "--log-level",
        default="warning",
        choices=["debug", "info", "warning", "error", "critical"],
        help="RapidOCR log level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = ROOT / image_path

    if not image_path.exists():
        print(f"Image not found: {image_path}")
        return 1

    ocr = RapidOCR(
        params={
            "Global.model_root_dir": str(MODEL_DIR),
            "Global.log_level": args.log_level,
        }
    )
    result = ocr(image_path)

    if args.json:
        print(json.dumps(result.to_json(), ensure_ascii=False, indent=2, default=str))
        return 0

    if not result.txts:
        print("No text detected.")
        return 0

    for text, score in zip(result.txts, result.scores):
        print(f"{score:.5f}\t{text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
