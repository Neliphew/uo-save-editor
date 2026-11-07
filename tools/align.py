#!/usr/bin/env python3
"""Discover the mapping between a Debug enum section and an FMSB message sheet.

The port's .inc dumps (Debug/_UcEnum_*.inc) list an id space with a Japanese
comment per id; the localisation sheets (MsgSheet/*.fms in Unicorn*.CPK) hold a
flat string pool.  This module parses both and scores candidate index offsets
(``fms_index = enum_index + k``) so the mapping can be established by evidence
instead of assumption.

Usage:
    python align.py              # probe every known enum/sheet pair, print best offsets
"""
from __future__ import annotations

import json
import struct
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 提取出来的游戏数据目录（默认 tools/_data，可用环境变量 UO_DATA_DIR 覆盖）
import os
DATA = Path(os.environ.get("UO_DATA_DIR", HERE / "_data"))
JP_DIR = DATA / "extract" / "MsgSheet"
CN_DIR = HERE / "fms_cn" / "MsgSheet"
DEBUG_DIR = DATA / "extract" / "Debug"


# --------------------------------------------------------------------------- #
# FMSB (MsgSheet) reader -- header 0x20, count at 0x14, count*8 zero slots,
# count NUL-terminated UTF-8 strings, 16-byte align, 16-byte FEOC footer.
# --------------------------------------------------------------------------- #
def parse_fms(path: Path) -> list[str]:
    data = Path(path).read_bytes()
    if data[:4] != b"FMSB":
        raise ValueError(f"{path}: not an FMSB sheet")
    count = struct.unpack_from("<I", data, 0x14)[0]
    at = 0x20 + 8 * count
    end = len(data) - 16
    if data[end:end + 4] != b"FEOC":
        raise ValueError(f"{path}: missing FEOC footer")
    strings: list[str] = []
    for _ in range(count):
        stop = data.find(b"\0", at, end)
        if stop < 0:
            raise ValueError(f"{path}: fewer strings than entries")
        strings.append(data[at:stop].decode("utf-8"))
        at = stop + 1
    return strings


# --------------------------------------------------------------------------- #
# Debug enum dumps: "IDENT , // comment" lines between "SECTION:count" headers.
# --------------------------------------------------------------------------- #
def parse_inc(path: Path) -> dict[str, list[tuple[int, str, str]]]:
    sections: dict[str, list[tuple[int, str, str]]] = {}
    current: list[tuple[int, str, str]] | None = None
    for raw in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if "," not in line:
            # section header like "CLASSTYPE:74" or "CLASSTYPE_END"
            name = line.split(":", 1)[0]
            if name.endswith("_END"):
                current = None
            else:
                current = sections.setdefault(name, [])
            continue
        if current is None:
            continue
        ident, _, rest = line.partition(",")
        comment = rest.split("//", 1)[1].strip() if "//" in rest else ""
        current.append((len(current), ident.strip(), comment))
    return sections


def norm(text: str) -> str:
    """Comparison key: NFKC, no spaces, no markup."""
    text = unicodedata.normalize("NFKC", text)
    for token in ("#c(10)", "#c(11)", "#c(13)", "#/c", "#i", "#/i"):
        text = text.replace(token, "")
    return "".join(text.split())


def score(sheet: list[str], entries: list[tuple[int, str, str]], k: int) -> tuple[int, int, list]:
    hits, bad = 0, []
    for idx, ident, comment in entries:
        want = norm(comment)
        at = idx + k
        if not want or at < 0 or at >= len(sheet):
            continue
        got = norm(sheet[at])
        if got and want == got:
            hits += 1
        elif got:
            bad.append((idx, want, got))
    return hits, len(bad), bad


def best_offset(sheet: list[str], entries: list[tuple[int, str, str]], lo=-80, hi=160):
    best = (-1, None)
    for k in range(lo, hi):
        hits, bad, _ = score(sheet, entries, k)
        if hits > best[0]:
            best = (hits, k)
    return best


PAIRS = [
    # sheet (JP), inc file, enum section
    ("UcCharaList.fms", "_UcEnum_CharaSet.inc", "CHARASET"),
    ("UcClassList.fms", "_UcEnum_Class.inc", "CLASSTYPE"),
    ("UcItemList.fms", "_UcEnum_Item.inc", "ITEMID"),
    ("UcSkillList.fms", "_UcEnum_Skill.inc", "BT_SKILLID"),
    ("UcBuffList.fms", "_UcEnum_Buff.inc", "EFFECTID"),
    ("UcUnitList.fms", "_UcEnum_CharaNpc.inc", "NPCTYPE"),
]


def main() -> None:
    for sheet_name, inc_name, section in PAIRS:
        sheet = parse_fms(JP_DIR / sheet_name)
        sections = parse_inc(DEBUG_DIR / inc_name)
        entries = sections.get(section)
        if entries is None:
            print(f"{sheet_name}: section {section} not found in {inc_name}")
            continue
        hits, k = best_offset(sheet, entries)
        coverage = len([e for e in entries if e[2]])
        print(f"{sheet_name}: sheet={len(sheet):5d} {section}={len(entries):5d} "
              f"best k={k!s:>5} hits={hits:4d}/{coverage} (commented entries)")


if __name__ == "__main__":
    main()
