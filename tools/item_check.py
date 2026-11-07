#!/usr/bin/env python3
"""Quality report for the item id -> MsgSheet index mapping (index = id + 30).

Collects every equipment id that appears in the current save plus all backup saves,
renders it through UcItemList and classifies the string as a plausible item name
(short, no sentence punctuation) or suspicious (long / multi-line).  Suspicious ids
are grouped so a secondary offset can be spotted.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "editor"))

import uo_core  # noqa: E402

from align import CN_DIR, parse_fms  # noqa: E402

ITEM_BASE = 30
SAVE_ROOT = Path(r"F:\Unicorn Overlord\data\user")


def collect_equipment_ids() -> Counter:
    ids: Counter = Counter()
    seen = set()
    for path in SAVE_ROOT.rglob("UCSAVEFILE.DATA"):
        key = (path.stat().st_size, path.stat().st_mtime_ns)
        if key in seen:
            continue
        seen.add(key)
        try:
            save = uo_core.load_save(str(path))
        except (OSError, ValueError):
            continue
        for char in save.characters:
            if not char.in_use:
                continue
            for item in char.equipment:
                if item:
                    ids[item] += 1
            # inventory slots too
            for slot in save.items:
                if slot.id:
                    ids[slot.id] += 1
    return ids


def looks_like_name(text: str) -> bool:
    if not text or text in ("-",):
        return False
    if "\n" in text or "。" in text or len(text) > 14:
        return False
    return True


def main() -> int:
    items = parse_fms(CN_DIR / "UcItemList.fms")
    ids = collect_equipment_ids()
    bad: dict[int, str] = {}
    good = 0
    for ident in sorted(ids):
        at = ident + ITEM_BASE
        text = items[at] if 0 <= at < len(items) else "(out of range)"
        if looks_like_name(text):
            good += 1
        else:
            bad[ident] = text
    print(f"distinct item ids seen: {len(ids)}  good={good}  suspicious={len(bad)}")
    print(f"id range: {min(ids)}..{max(ids)}")
    print("\nsuspicious ids (id -> sheet text at id+30):")
    for ident in sorted(bad)[:80]:
        print(f"  {ident:5d} -> {bad[ident]!r}")
    print(f"\nsheet length {len(items)}; ids up to {len(items) - 1 - ITEM_BASE} render in-range")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
