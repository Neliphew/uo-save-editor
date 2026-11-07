"""Cross-validate the generated name tables against real save files.

Renders characters / classes / equipment (two-hop: equipment value == inventory
record index, not item id) for the richest save found, and reports any id that
has no name.

Usage:  python verify_names.py [path-to-UCSAVEFILE.DATA]
"""
from __future__ import annotations

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "editor"))

import uo_core  # noqa: E402


def richest_save() -> str:
    saves = glob.glob(os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                   "data", "user", "**", "UCSAVEFILE.DATA"),
                      recursive=True)
    best = (0, None)
    for path in saves:
        try:
            save = uo_core.load_save(path)
        except Exception:
            continue
        used = sum(1 for c in save.characters if c.in_use)
        if used > best[0]:
            best = (used, path)
    return best[1]


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else richest_save()
    names = uo_core.load_names(HERE)
    save = uo_core.load_save(path)
    by_index = {it.index: it for it in save.items}
    print("save:", path)
    print("tables:", {k: len(v) for k, v in names.items()},
          "| inventory records:", len(save.items))

    missing, resolved = 0, 0
    for char in save.characters:
        if not char.in_use:
            continue
        labels = []
        for value in char.equipment:
            if not value:
                labels.append("")
                continue
            item = by_index.get(value)
            if item is None:
                labels.append(f"<no index {value}>")
                missing += 1
                continue
            name = names["items"].get(item.id)
            resolved += 1
            if name is None:
                missing += 1
                name = f"<no name for item {item.id}>"
            labels.append(name)
        print(f"#{char.index:3d} id={char.id:4d} lv={char.lv:2d} "
              f"{names['classes'].get(char.class_id, '?'):<6s} "
              f"{names['chars'].get(char.name_id, '?'):<10s} {labels}")
    print(f"\nequipment slots resolved via inventory index: {resolved}, "
          f"unresolved: {missing}")


if __name__ == "__main__":
    main()
