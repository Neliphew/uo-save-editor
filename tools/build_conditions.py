"""Build tactic-condition (EQUIPAI_IF) + skill-category Chinese labels.

Sources (all extracted from the CPK archives):
    _analysis/names/fms_cn/MsgSheet/UcFactorList.fms   (CN, simplified Chinese)
    _analysis/data/extract/MsgSheet/UcFactorList.fms   (base CPK, Japanese)
    _analysis/data/extract/Debug/_UcEnum_EquipAi.inc   (EQUIPAI_IF:203 + JP comments)
    _analysis/data/extract/Debug/_UcEnum_Skill.inc     (EQUIPAI_*_SKILL_LV1..4 order)
    _analysis/names/fms_cn/MsgSheet/UcSkillList.fms    (idx 489..496 = category labels)

UcFactorList.fms is one pool of 624 strings, laid out as four parallel blocks for the
203 entry EQUIPAI_IF enum:

    idx   0..202  long condition text, with markup + "%s" target placeholder
                  e.g. 16 -> "HP不高于#c(14)25％#/c#(55)的%s"
    idx 203..405  the same 203 conditions as short plain labels (id = idx - 203)
                  e.g. 16 -> "HP不高于25％的"
    idx 406..608  long help descriptions (id = idx - 406; idx 406 is empty = id 0)
    idx 609..623  condition category captions (队列·状况 / 兵种 / HP / …)

Outputs (dict form {"<id>": "…"}):
    conditions.json       id 0..202, short plain label      <- GUI primary
    conditions_full.json  id 0..202, full text w/ markup, %s kept
    conditions_desc.json  id 0..202, help text (markup stripped)
    enums.json            skill categories 3..10 + factor category captions
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from align import parse_fms  # noqa: E402

CN_DIR = os.path.join(HERE, "fms_cn", "MsgSheet")
JP_DIR = os.path.join(os.path.dirname(HERE), "data", "extract", "MsgSheet")
DEBUG_DIR = os.path.join(os.path.dirname(HERE), "data", "extract", "Debug")

COND_COUNT = 203  # _UcEnum_EquipAi.inc: EQUIPAI_IF:203

_MARKUP = re.compile(r"#\(\d+\)|#c\(\d+\)|#/c|##\([A-Z]+\)|#\([A-Z]+\)")


def strip_markup(text: str) -> str:
    return _MARKUP.sub("", text).replace("\n", " ").strip()


def parse_inc_names(path: str) -> tuple[str, list[str], list[str]]:
    """Return (header_key, [NAMES], [comments]) from a Debug enum dump."""
    names: list[str] = []
    comments: list[str] = []
    key = ""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if ":" in line and "//" not in line:
                key = line
                continue
            if line.endswith("_END"):
                break
            name, _, comment = line.partition("//")
            names.append(name.strip().strip(","))
            comments.append(comment.strip())
    return key, names, comments


def build() -> dict:
    cn = parse_fms(os.path.join(CN_DIR, "UcFactorList.fms"))
    jp = parse_fms(os.path.join(JP_DIR, "UcFactorList.fms"))
    key, enum_names, enum_comments = parse_inc_names(
        os.path.join(DEBUG_DIR, "_UcEnum_EquipAi.inc"))
    assert key == "EQUIPAI_IF:203", key
    assert len(enum_names) == COND_COUNT, len(enum_names)

    out = {"conditions": {}, "full": {}, "desc": {}, "enums": {}}

    for ident in range(COND_COUNT):
        long_cn = cn[ident]
        short_cn = cn[ident + 203]
        desc_cn = cn[ident + 406]
        out["full"][ident] = long_cn
        out["conditions"][ident] = short_cn or strip_markup(long_cn).replace("%s", "").strip()
        if desc_cn:
            out["desc"][ident] = strip_markup(desc_cn)

    # ---- skill categories 3..10 (EQUIPAI_*_SKILL_LV1..4) ----
    _, skill_names, skill_comments = parse_inc_names(
        os.path.join(DEBUG_DIR, "_UcEnum_Skill.inc"))
    skill_pool = parse_fms(os.path.join(CN_DIR, "UcSkillList.fms"))
    cat_ids = []
    for idx, name in enumerate(skill_names):
        if name.startswith("EQUIPAI_ACTIVE_SKILL_LV") or name.startswith("EQUIPAI_PASSIVE_SKILL_LV"):
            cat_ids.append((idx, name, skill_comments[idx]))
    assert len(cat_ids) == 8, cat_ids

    category_labels = {}  # category enum value -> Chinese label
    for pos, (enum_id, name, comment) in enumerate(cat_ids):
        label = skill_pool[489 + pos]  # 主动技能Lv１ .. 被动技能Lv４
        category_labels[enum_id] = label
    out["enums"]["skill_categories"] = category_labels
    out["enums"]["skill_category_enum_names"] = {i: n for i, n, _ in cat_ids}

    # ---- condition category captions (pool tail, enum unidentified) ----
    out["enums"]["factor_category_captions"] = {
        i: cn[i] for i in range(609, 624) if cn[i]
    }

    # ---- verification numbers ----
    matched = 0
    for ident in range(COND_COUNT):
        comment = enum_comments[ident]
        text = strip_markup(jp[ident]).replace("%s", "")
        if comment and comment not in ("なし", "？？？") and (comment in text or text.startswith(comment)):
            matched += 1
    out["_verify"] = {
        "cn_pool_len": len(cn),
        "jp_pool_len": len(jp),
        "enum_entries": len(enum_names),
        "jp_comment_match": matched,
        "empty_short_labels": sum(1 for i in range(COND_COUNT) if not cn[i + 203]),
        "skill_category_ids": [i for i, _, _ in cat_ids],
    }
    return out


def write_json(name: str, data: dict) -> None:
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in sorted(data.items())}, fh,
                  ensure_ascii=False, indent=0)
        fh.write("\n")
    print(f"{name:24s} {len(data):4d} entries")


def main() -> None:
    data = build()
    write_json("conditions.json", data["conditions"])
    write_json("conditions_full.json", data["full"])
    write_json("conditions_desc.json", data["desc"])
    write_json("enums.json", data["enums"])
    # list form for consistency with the other tables
    os.makedirs(os.path.join(HERE, "list"), exist_ok=True)
    with open(os.path.join(HERE, "list", "conditions.json"), "w", encoding="utf-8") as fh:
        json.dump([{"id": i, "cn": data["conditions"][i]} for i in sorted(data["conditions"])],
                  fh, ensure_ascii=False, indent=0)
        fh.write("\n")
    print("\nverify:", json.dumps(data["_verify"], ensure_ascii=False))
    print("\nconditions[0..8] :", [data["conditions"].get(i) for i in range(9)])
    print("conditions[16,17,18,24,25]:", [data["conditions"].get(i) for i in (16, 17, 18, 24, 25)])
    print("full[16]         :", data["full"][16])
    print("desc[16]         :", data["desc"].get(16))
    print("skill_categories :", data["enums"]["skill_categories"])


if __name__ == "__main__":
    main()
