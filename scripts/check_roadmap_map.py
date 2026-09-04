#!/usr/bin/env python3
"""Tripwire lint: the public roadmap keeps up with the private one.

docs/roadmap.md is a deliberate projection of the private .devdocs/ROADMAP.md —
fewer facts, grouped by user outcome instead of by work. Two things rot such a
projection: an item added to the private roadmap that nobody decided about
publicly, and a public group left standing after the work behind it moved.

.devdocs/roadmap-public-map.tsv ties the two together, and this lint proves the
tie is total in both directions:

  * every Phase B-E item in ROADMAP.md is covered by a map row (a row may send
    it to `-`, meaning deliberately not published — the lint wants a decision,
    not a publication);
  * every group a map row names exists as a `## ` heading in docs/roadmap.md;
  * every group heading in docs/roadmap.md is named by at least one map row.

Phase A is out of scope: those are core fixes, not roadmap themes.

.devdocs is a symlink into a private repo and is absent from a public clone, so
this exits 0 with a note when the map is missing. That keeps `make checks-fast`
green in CI, where the private side cannot be seen.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROADMAP = ROOT / ".devdocs" / "ROADMAP.md"
MAP = ROOT / ".devdocs" / "roadmap-public-map.tsv"
PUBLIC_ROADMAP = ROOT / "docs" / "roadmap.md"

# Sections of docs/roadmap.md that are page furniture, not feature groups.
FIXED_SECTIONS = {"Recently shipped", "Suggesting something"}

_PHASE = re.compile(r"^##\s+Phase\s+([A-Z])\b")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ROW = re.compile(r"^\|\s*(\d+)\s*\|")


def private_items() -> set[str]:
    """Item numbers from the Phase B-E tables of the private roadmap."""
    items: set[str] = set()
    in_scope = False
    for line in PRIVATE_ROADMAP.read_text(encoding="utf-8").splitlines():
        phase = _PHASE.match(line)
        if phase:
            in_scope = phase.group(1) in {"B", "C", "D", "E"}
            continue
        if line.startswith("## "):
            in_scope = False
            continue
        if in_scope:
            row = _ROW.match(line)
            if row:
                items.add(row.group(1))
    return items


def map_rows() -> list[tuple[str, str]]:
    """(id, group) pairs from the map, comments and the header dropped."""
    rows = []
    for raw in MAP.read_text(encoding="utf-8").splitlines():
        # A comment starts `#` + space/`#`; an issue id starts `#` + digit.
        if not raw.strip() or re.match(r"^#(?!\d)", raw):
            continue
        parts = raw.split("\t")
        if len(parts) < 2 or parts[0] == "id":
            continue
        rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def public_groups() -> set[str]:
    headings = set()
    for line in PUBLIC_ROADMAP.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        if heading:
            headings.add(heading.group(1))
    return headings - FIXED_SECTIONS


def main() -> int:
    if not MAP.exists() or not PRIVATE_ROADMAP.exists():
        print("Roadmap map lint: skipped — .devdocs is not present "
              "(expected in a public clone).")
        return 0

    # The private map can land before the public page does — the page arrives on
    # a feature branch, .devdocs commits to its own repo immediately. Warn rather
    # than fail, so a release from a branch without the page is not blocked.
    if not PUBLIC_ROADMAP.exists():
        print("Roadmap map lint: WARNING — roadmap-public-map.tsv exists but "
              f"{PUBLIC_ROADMAP.relative_to(ROOT)} does not. Nothing checked.")
        return 0

    problems: list[str] = []
    rows = map_rows()
    mapped_ids = {rid for rid, _ in rows}
    groups = public_groups()

    # 1. Every Phase B-E item has a decision. `9` is covered by `9a`, `9b`, ...
    for item in sorted(private_items(), key=int):
        if not any(rid == item or rid.startswith(item) and rid[len(item):].isalpha()
                   for rid in mapped_ids):
            problems.append(
                f"ROADMAP.md item {item} has no row in roadmap-public-map.tsv — "
                f"decide whether docs/roadmap.md mentions it, or map it to `-`."
            )

    # 2. Every group a row points at exists on the public page.
    for rid, group in rows:
        if group != "-" and group not in groups:
            problems.append(
                f"map row {rid} points at group {group!r}, which is not a "
                f"`## ` heading in docs/roadmap.md."
            )

    # 3. No public group is left standing with nothing behind it.
    referenced = {group for _, group in rows}
    for group in sorted(groups - referenced):
        problems.append(
            f"docs/roadmap.md group {group!r} is named by no map row — "
            f"remove it, or add the item it represents."
        )

    if problems:
        print(f"Roadmap map lint: {len(problems)} problem(s)\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"Roadmap map lint: {len(private_items())} private items decided, "
          f"{len(groups)} public groups backed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
