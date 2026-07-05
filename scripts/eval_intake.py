#!/usr/bin/env python3
"""Score a vision-provider response against the intake benchmark fixture.

The fixture (tests/fixtures/intake/eleven_books.jpg + .groundtruth.json) is
an 11-book shelf photo with human-verified titles. This script is the
regression benchmark for any future model swap: run it, record the score.

Usage:
    # Score a saved provider response: {"books": [{"title": ..., "authors": ...}]}
    python scripts/eval_intake.py response.json

    # Call the provider configured in the app database on the fixture photo
    python scripts/eval_intake.py --live

Output: titles correct / missed / hallucinated, using the same fuzzy title
normalizer the tile-merge dedup uses (services/vision.py).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.vision import MERGE_SIMILARITY, _normalize_title  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "intake"
PHOTO = FIXTURE_DIR / "eleven_books.jpg"
GROUND_TRUTH = FIXTURE_DIR / "eleven_books.groundtruth.json"


def score(detected: list[dict], truth: list[dict]) -> dict:
    """Match detected books to ground-truth titles (fuzzy, greedy)."""
    import difflib

    remaining = {i: _normalize_title(b["title"]) for i, b in enumerate(truth)}
    correct, hallucinated = [], []
    for book in detected:
        key = _normalize_title(book.get("title") or "")
        match = None
        for i, truth_key in remaining.items():
            if key == truth_key or difflib.SequenceMatcher(
                    None, key, truth_key).ratio() >= MERGE_SIMILARITY:
                match = i
                break
        if match is not None:
            correct.append({"detected": book["title"], "truth": truth[match]["title"]})
            del remaining[match]
        else:
            hallucinated.append(book["title"])
    missed = [truth[i]["title"] for i in remaining]
    return {"correct": correct, "missed": missed, "hallucinated": hallucinated}


async def _live_detect() -> list[dict]:
    from app.database import get_db, get_all_settings
    from app.services import vision

    with get_db() as db:
        settings = get_all_settings(db)
    if not settings.get("vision_provider"):
        sys.exit("No vision provider configured — set one up in Settings → Integrations")
    image_bytes = PHOTO.read_bytes()
    return await vision.detect_spines([(image_bytes, "image/jpeg")], settings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", nargs="?", help="JSON file with a provider response")
    parser.add_argument("--live", action="store_true",
                        help="call the configured provider on the fixture photo")
    args = parser.parse_args()

    if args.live:
        detected = asyncio.run(_live_detect())
    elif args.response:
        payload = json.loads(Path(args.response).read_text())
        detected = payload["books"] if isinstance(payload, dict) else payload
    else:
        parser.error("pass a response JSON file or --live")

    truth = json.loads(GROUND_TRUTH.read_text())
    result = score(detected, truth)

    n = len(truth)
    print(f"Detected {len(detected)} books against {n} ground-truth titles\n")
    print(f"Correct:      {len(result['correct'])}/{n}")
    for m in result["correct"]:
        note = "" if m["detected"] == m["truth"] else f"  (as {m['detected']!r})"
        print(f"  ✓ {m['truth']}{note}")
    print(f"Missed:       {len(result['missed'])}")
    for t in result["missed"]:
        print(f"  ✗ {t}")
    print(f"Hallucinated: {len(result['hallucinated'])}")
    for t in result["hallucinated"]:
        print(f"  ? {t}")
    return 0 if not result["missed"] and not result["hallucinated"] else 1


if __name__ == "__main__":
    sys.exit(main())
