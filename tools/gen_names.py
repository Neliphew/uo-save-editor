"""Generate Chinese name tables (JSON) for the save-editor GUI.

Source: CRI CPK message sheets (`.fms`) extracted from Unicorn_CN.CPK
(simplified Chinese overlay) by ../data/extract.py.

.fms format (FMSB, see REPORT.md):
    0x00  b"FMSB"
    0x04  u32  file_size - 0x30
    0x08  u32  0x20 (data start)
    0x0C  u32  0
    0x10  u32  0
    0x14  u32  count          <- number of strings
    0x18  u32  3 (version)
    0x1C  u32  0
    0x20  row table: count * 8 bytes (all zero in shipped files)
    then  count * NUL-terminated UTF-8 strings
    zero padding to 16, then 16-byte b"FEOC" chunk (u32 size = 0x10)

Index mapping rules (validated against Debug/_UcEnum_*.inc and real saves):
    chars    id -> UcCharaList[i]                  (CHARASET index, 1388 entries)
    classes  id -> UcClassList[id]                 (ids 0..73 = CLASSTYPE, 74 empty)
    skills   id -> UcSkillList[id + 15]            (BT_SKILLID, 471/471 exact)
    items    id -> UcItemList[id + 30]   for   0 <= id <= 281
                -> UcItemList[id + 311]  for 282 <= id <= 982   (ITEMID: 983 ids)
             NOTE: character equipment slots (+76..+91) do NOT hold item ids; they hold
             the *inventory record index* (item record +4).  Resolve with
             ``item_id = items_by_index[value].id`` before looking up a name.
    names    id -> UcNameList[id]                  (speaker / display names, 68 entries)

Written files (dict form, what uo_core.load_names() expects):
    chars.json  classes.json  items.json  skills.json  names.json
Plus list form under list/ : [{"id": 1, "cn": "…"}, …]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from align import parse_fms, CN_DIR  # noqa: E402

OUT_DICT = HERE
OUT_LIST = os.path.join(HERE, "list")


def sheet(name: str) -> list[str]:
    return parse_fms(os.path.join(CN_DIR, name))


def item_name_table() -> dict[int, str]:
    pool = sheet("UcItemList.fms")
    n = len(pool)
    table: dict[int, str] = {}
    for ident in range(0, 983):  # ITEMID: 983
        idx = ident + 30 if ident <= 281 else ident + 311
        if 0 <= idx < n and pool[idx]:
            table[ident] = pool[idx]
    return table


def skill_name_table() -> dict[int, str]:
    pool = sheet("UcSkillList.fms")
    table: dict[int, str] = {}
    for ident in range(0, 471):  # BT_SKILLID: 471
        idx = ident + 15
        if 0 <= idx < len(pool) and pool[idx]:
            table[ident] = pool[idx]
    return table


def char_name_table() -> dict[int, str]:
    pool = sheet("UcCharaList.fms")
    return {i: s for i, s in enumerate(pool) if s}


def class_name_table() -> dict[int, str]:
    pool = sheet("UcClassList.fms")
    return {i: pool[i] for i in range(0, 74) if i < len(pool) and pool[i]}


def display_name_table() -> dict[int, str]:
    pool = sheet("UcNameList.fms")
    return {i: s for i, s in enumerate(pool) if s}


def write_pair(stem: str, table: dict[int, str]) -> None:
    with open(os.path.join(OUT_DICT, stem + ".json"), "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in sorted(table.items())}, fh,
                  ensure_ascii=False, indent=0)
        fh.write("\n")
    os.makedirs(OUT_LIST, exist_ok=True)
    with open(os.path.join(OUT_LIST, stem + ".json"), "w", encoding="utf-8") as fh:
        json.dump([{"id": k, "cn": v} for k, v in sorted(table.items())], fh,
                  ensure_ascii=False, indent=0)
        fh.write("\n")


def main() -> None:
    tables = {
        "chars": char_name_table(),
        "classes": class_name_table(),
        "items": item_name_table(),
        "skills": skill_name_table(),
        "names": display_name_table(),
    }
    for stem, table in tables.items():
        write_pair(stem, table)
        print(f"{stem:8s} {len(table):5d} entries")
    print("\nchars[0..4] :", [tables["chars"].get(i) for i in range(5)])
    print("classes[0..4]:", [tables["classes"].get(i) for i in range(5)])
    print("items[0,1,2,8,282,487,605,1014,1114,1191]:",
          [tables["items"].get(i) for i in (0, 1, 2, 8, 282, 487, 605, 1014, 1114, 1191)])
    print("skills[0,1,31,43,44,471]:",
          [tables["skills"].get(i) for i in (0, 1, 31, 43, 44, 471)])
    print("names[0..4]  :", [tables["names"].get(i) for i in range(5)])


if __name__ == "__main__":
    main()
