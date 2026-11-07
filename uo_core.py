r"""uo_core.py -- Unicorn Overlord (PS4/PC port) save-file editor core library.

This module is the importable backend for the Tkinter save editor
(``UOEditor.py``).  It is a read/write wrapper around
``data/user/savedata/1/CUSA36381/<SLOT>/UCSAVEFILE.DATA`` and never touches
anything outside the file you explicitly hand to :meth:`SaveData.write`.

Quick start
-----------
    import uo_core
    s = uo_core.load_save(r"data\user\savedata\1\CUSA36381\SAVEDATA31\UCSAVEFILE.DATA")
    print(s.header.money, len(s.characters))
    c = s.characters[0]
    print(c.id, c.lv, c.exp, c.plus)
    s.set_char(0, lv=50)          # also writes the matching EXP total
    s.set_money(9999999)
    s.write(r"D:\tmp\UCSAVEFILE.DATA")   # refuses any path containing "data"

Format facts (all verified; see _analysis/save/REPORT.md)
---------------------------------------------------------
* file size is fixed: 5,109,536 bytes (0x4DF720), magic "UCSD" at 0x04.
* character array: 0x2AF40, stride 464, max 500, terminated by ID==0xFFFFFFFF.
* per character: +0 ID u32, +40 class u8, +52 name id u16, +56 exp u32,
  +60 lv u16, +64..73 ten "plus" u8 stats, +76 4*equipment u32, +460 flags
  (bit5 = NOT in use).
* the game recomputes level from EXP when a save is loaded, so setting a level
  must also set the cumulative EXP total -- :func:`exp_for_level` gives the
  exact value from the executable table at eboot 0x113FEB0.
* no checksum / hash / signature exists inside the file, nothing to fix up
  after editing.
* fields that must NOT be edited: the 24-byte per-character block at +0x160,
  the counters at 0x63980/0x63984, and the per-character value at +62.

Levels / EXP
------------
``TOTAL`` holds the 49 cumulative EXP thresholds of the level table
(level 2 needs TOTAL[0], level 3 needs TOTAL[1], ... level 50 needs TOTAL[48]):
    exp_for_level(L)  -> TOTAL[L-2] for L>=2, 0 for L<=1
    level_for_exp(e)  -> 1 + number of thresholds <= e
"""

from __future__ import annotations

import json
import os
import shutil
import struct
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- #
# container constants
# --------------------------------------------------------------------------- #
FILESIZE = 0x4DF720
MAGIC = b"UCSD"

CHAR_BASE = 0x2AF40
CHAR_STRIDE = 464
CHAR_MAX = 500
CHAR_TERMINATOR = 0xFFFFFFFF

CHAR_ID = 0
CHAR_FORMATION = 4
CHAR_CLASS = 40
CHAR_NAME_ID = 52
CHAR_EXP = 56
CHAR_LV = 60
CHAR_PLUS = 64          # 10 bytes
CHAR_EQUIPMENT = 76     # 4 * u32
CHAR_FLAGS = 460        # bit 5 = NOT in use
CHAR_RESERVED_0X160 = 0x160  # 24 byte per-character block: never edit

CHR_NEXT_ID = 0x63980    # u32 next free character id  : never edit
CHR_COUNT = 0x63984      # u32 used record count       : never edit

ITEM_BASE = 0xA0
ITEM_STRIDE = 20
ITEM_MAX = 3800
ITEM_ID = 0
ITEM_INDEX = 4
ITEM_COUNT = 8          # 3 bytes
ITEM_EQUIP1 = 11
ITEM_EQUIP2 = 12
ITEM_STATUS = 16

BOND_BASE = 0x1B5830
BOND_STRIDE = 1316
BOND_MAX = 164
BOND_ENTRY_SIZE = 8     # partner id u32 + value u16 + flag u16
BOND_VALUE_MAX = 1000   # highest friendship value seen in real saves / editor cap
BOND_FLAG_MAX = 3       # observed range of the second u16 (bond conversation count)

ITEM_COUNT_MAX = 999    # editor cap for one inventory stack

# 16 slots x 16 bytes, right after the four equipment u32 values.
# Occupied slot layout (both kinds keep the 0xFFFF sentinel at +8):
#   skill  -> (u32 skill_id, u32 category_flag 3..10, u32 0xFFFF, u32 0)
#   tactic -> (u32 condition_id 0..202, u32 skill_id, u32 slot_no 0..3, u32 4)
SKILL_BASE = 0x5C
SKILL_SLOT_SIZE = 16
SKILL_SLOT_COUNT = 16
SKILL_EMPTY = (0, 0, 0xFFFF, 0)
SKILL_ID_MAX = 470      # highest skill id present in names/skills.json

#: category flag (slot +4 of a skill entry) -> slot label.  The flag mirrors
#: skill ids 3..10 whose names in the game message table are A.Lv1..P.Lv4, i.e.
#: the eight ability slots of a character (4 active + 4 passive).
SKILL_CATEGORY_NAMES = {
    3: "主动技能槽1",
    4: "主动技能槽2",
    5: "主动技能槽3",
    6: "主动技能槽4",
    7: "被动技能槽1",
    8: "被动技能槽2",
    9: "被动技能槽3",
    10: "被动技能槽4",
}

UNIT_BASE = 0x10D89A
UNIT_STRIDE = 1720
UNIT_MAX = 10

HEADER_MONEY = 0x20
HEADER_RENOWN = 0x24
HEADER_PLAYTIME = 0x1C
HEADER_SLOT = 0x10
HEADER_DATETIME = 0x14
HEADER_VERSION = 0x08
HEADER_FILESIZE = 0x0C

PLUS_NAMES = (
    "HPPlus", "AttackPlus", "DefensePlus", "MagicAttackPlus",
    "MagicDefensePlus", "HitRatePlus", "AVoidPlus", "CriticalPlus",
    "GuardPlus", "SpeedPlus",
)

#: Cumulative EXP thresholds from the executable level table (eboot 0x113FEB0).
TOTAL = [
    100, 300, 600, 1000, 1500, 2100, 2800, 3650, 4650, 5800,
    7100, 8600, 10350, 12350, 14700, 17400, 20500, 24000, 28000, 32500,
    37500, 43100, 49400, 56400, 64200, 72900, 82500, 93200, 105300, 118800,
    133900, 150600, 169300, 190000, 213100, 238800, 267300, 299000, 334300, 373600,
    417500, 466200, 520500, 581000, 648500, 723500, 807000, 900000, 1000000,
]
MAX_LEVEL = len(TOTAL) + 1   # 50
MIN_LEVEL = 1

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def exp_for_level(lv: int) -> int:
    """Cumulative EXP total required for level ``lv`` (1..50)."""
    lv = int(lv)
    if lv <= MIN_LEVEL:
        return 0
    if lv > MAX_LEVEL:
        raise ValueError(f"level must be in 1..{MAX_LEVEL}, got {lv}")
    return TOTAL[lv - 2]


def level_for_exp(exp: int) -> int:
    """Level implied by a cumulative EXP total (inverse of exp_for_level)."""
    exp = int(exp)
    lv = MIN_LEVEL
    for threshold in TOTAL:
        if exp >= threshold:
            lv += 1
        else:
            break
    return lv


def exp_for_next_level(lv: int) -> Optional[int]:
    """EXP threshold of the next level, or None when already at MAX_LEVEL."""
    lv = int(lv)
    if lv >= MAX_LEVEL:
        return None
    return exp_for_level(lv + 1)


def char_offset(index: int) -> int:
    """Absolute file offset of character record ``index`` (0-based)."""
    index = int(index)
    if not 0 <= index < CHAR_MAX:
        raise IndexError(f"character index out of range: {index}")
    return CHAR_BASE + index * CHAR_STRIDE


class AttrDict(dict):
    """dict whose keys are also readable/writable as attributes."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class _Record:
    """Base class: attribute + item access on top of a fixed field set."""

    _FIELDS: tuple = ()

    def __getitem__(self, name: str) -> Any:
        if name in self._FIELDS:
            return getattr(self, name)
        raise KeyError(name)

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self[name]
        except KeyError:
            return default

    def keys(self) -> tuple:
        return self._FIELDS

    def as_dict(self) -> dict:
        return {k: self[k] for k in self._FIELDS}

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.as_dict()}>"


# --------------------------------------------------------------------------- #
# records (live views on the mutable buffer)
# --------------------------------------------------------------------------- #
class Character(_Record):
    """Live view of one 464-byte character record.

    All properties read (and setters write) the owning :class:`SaveData`
    buffer, so the object never goes stale after :meth:`SaveData.set_char`.
    """

    _FIELDS = (
        "index", "file_off", "id", "class_id", "name_id", "lv", "exp",
        "plus", "equipment", "formation", "flags", "in_use",
    )

    def __init__(self, save: "SaveData", index: int):
        object.__setattr__(self, "_save", save)
        object.__setattr__(self, "index", int(index))
        object.__setattr__(self, "file_off", char_offset(index))

    # -- read-only identity -------------------------------------------------
    @property
    def id(self) -> int:
        return self._save._u32(self.file_off + CHAR_ID)

    @property
    def class_id(self) -> int:
        return self._save.buffer[self.file_off + CHAR_CLASS]

    @property
    def name_id(self) -> int:
        return self._save._u16(self.file_off + CHAR_NAME_ID)

    @property
    def formation(self) -> int:
        return self._save._u32(self.file_off + CHAR_FORMATION)

    # -- editable fields ----------------------------------------------------
    @property
    def lv(self) -> int:
        return self._save._u16(self.file_off + CHAR_LV)

    @lv.setter
    def lv(self, value: int) -> None:
        self._save.set_char(self.index, lv=value)

    @property
    def exp(self) -> int:
        return self._save._u32(self.file_off + CHAR_EXP)

    @exp.setter
    def exp(self, value: int) -> None:
        self._save.set_char(self.index, exp=value)

    @property
    def plus(self) -> list:
        off = self.file_off + CHAR_PLUS
        return list(self._save.buffer[off:off + 10])

    @plus.setter
    def plus(self, values: Iterable[int]) -> None:
        self._save.set_char(self.index, plus=values)

    @property
    def equipment(self) -> list:
        off = self.file_off + CHAR_EQUIPMENT
        return [self._save._u32(off + 4 * i) for i in range(4)]

    @equipment.setter
    def equipment(self, values: Iterable[int]) -> None:
        self._save.set_char(self.index, equipment=values)

    @property
    def flags(self) -> int:
        return self._save.buffer[self.file_off + CHAR_FLAGS]

    @property
    def in_use(self) -> bool:
        return not (self.flags & 0x20)

    @property
    def plus_named(self) -> dict:
        return dict(zip(PLUS_NAMES, self.plus))

    # -- skill / tactic slots ----------------------------------------------
    @property
    def skill_slots(self) -> list:
        """16 skill/tactic slots as a read-only snapshot.

        Each dict has keys ``idx``, ``file_off``, ``kind`` (``"skill"``,
        ``"tactic"`` or ``"empty"``), ``occupied``, ``skill_id``,
        ``category`` (skill entries), ``cond_id``/``slot_no`` (tactic
        entries) and ``flag`` (raw +12 u32, always 0/4).
        """
        slots = []
        for i in range(SKILL_SLOT_COUNT):
            off = self.file_off + SKILL_BASE + i * SKILL_SLOT_SIZE
            v0 = self._save._u32(off)
            v1 = self._save._u32(off + 4)
            v2 = self._save._u32(off + 8)
            v3 = self._save._u32(off + 12)
            occupied = v1 != 0
            if not occupied:
                kind = "empty"
            elif 3 <= v1 <= 10:
                kind = "skill"
            else:
                kind = "tactic"
            slots.append({
                "idx": i,
                "file_off": off,
                "kind": kind,
                "occupied": occupied,
                "raw_a": v0,
                # a skill entry packs (extra flags << 16) | skill id
                "skill_id": (v1 if kind == "tactic" else (v0 & 0xFFFF)),
                "extra": 0 if kind == "tactic" else ((v0 >> 16) & 0xFFFF),
                "category": v1 if kind == "skill" else 0,
                "cond_id": v0 if kind == "tactic" else 0,
                "slot_no": v2 if kind == "tactic" else 0,
                "flag": v3,
            })
        return slots

    @property
    def skill_slots_named(self) -> list:
        """Like :attr:`skill_slots` but with a ready-to-show ``label``."""
        out = []
        for slot in self.skill_slots:
            if slot["kind"] == "skill":
                label = SKILL_CATEGORY_NAMES.get(slot["category"], f"类别 {slot['category']}")
            elif slot["kind"] == "tactic":
                label = f"战术槽 {slot['slot_no'] + 1}（条件 {slot['cond_id']}）"
            else:
                label = "空槽"
            out.append(dict(slot, label=label))
        return out

    def __str__(self) -> str:
        return (f"#{self.index:3d} @0x{self.file_off:06X} id={self.id:4d} "
                f"cls={self.class_id:3d} name={self.name_id:5d} "
                f"lv={self.lv:2d} exp={self.exp:8d} plus={self.plus} "
                f"eq={self.equipment}" + ("" if self.in_use else " (unused)"))


class Item(_Record):
    """Live view of one 20-byte item record."""

    _FIELDS = ("slot", "index", "id", "count", "equip1", "equip2",
               "status", "file_off")

    def __init__(self, save: "SaveData", slot: int):
        object.__setattr__(self, "_save", save)
        object.__setattr__(self, "slot", int(slot))
        object.__setattr__(self, "file_off", ITEM_BASE + int(slot) * ITEM_STRIDE)

    @property
    def id(self) -> int:
        return self._save._u32(self.file_off + ITEM_ID)

    @property
    def index(self) -> int:
        return self._save._u32(self.file_off + ITEM_INDEX)

    @property
    def count(self) -> int:
        off = self.file_off + ITEM_COUNT
        return int.from_bytes(self._save.buffer[off:off + 3], "little")

    @count.setter
    def count(self, value: int) -> None:
        value = int(value)
        if not 0 <= value <= ITEM_COUNT_MAX:
            raise ValueError(f"item count must be 0..{ITEM_COUNT_MAX}, got {value}")
        off = self.file_off + ITEM_COUNT
        self._save.buffer[off:off + 3] = value.to_bytes(3, "little")

    @property
    def equip1(self) -> int:
        return self._save.buffer[self.file_off + ITEM_EQUIP1]

    @property
    def equip2(self) -> int:
        return self._save.buffer[self.file_off + ITEM_EQUIP2]

    @property
    def status(self) -> int:
        return self._save._u32(self.file_off + ITEM_STATUS)

    def __str__(self) -> str:
        return (f"slot={self.slot:4d} index={self.index:4d} id={self.id:6d} "
                f"count={self.count:3d} status=0x{self.status:08X}")


class Bond(_Record):
    """Live view of one 1316-byte bond/rapport record."""

    _FIELDS = ("index", "file_off", "id", "entries")

    def __init__(self, save: "SaveData", index: int):
        object.__setattr__(self, "_save", save)
        object.__setattr__(self, "index", int(index))
        object.__setattr__(self, "file_off", BOND_BASE + int(index) * BOND_STRIDE)

    @property
    def id(self) -> int:
        return self._save._u32(self.file_off)

    @property
    def entries(self) -> list:
        """[(other_character_id, points, rank), ...] for non-empty entries."""
        out = []
        for off, other in self._entry_offsets():
            packed = struct.unpack_from("<I", self._save.buffer, off + 4)[0]
            out.append((other, packed & 0xFFFF, packed >> 16))
        return out

    def _entry_offsets(self):
        """[(offset, partner_id), ...] for every stored bond entry."""
        out = []
        b = self._save.buffer
        for n in range((BOND_STRIDE - 4) // BOND_ENTRY_SIZE):
            off = self.file_off + 4 + n * BOND_ENTRY_SIZE
            other = struct.unpack_from("<I", b, off)[0]
            if other in (0, 0xFFFFFFFF):
                break
            out.append((off, other))
        return out

    def entry_offset(self, partner_id: int):
        """File offset of the entry for ``partner_id``, or ``None``."""
        for off, other in self._entry_offsets():
            if other == int(partner_id):
                return off
        return None

    def set_value(self, partner_id: int, value: int) -> "Bond":
        """Set the friendship points (0..1000) of one partner entry."""
        off = self.entry_offset(partner_id)
        if off is None:
            raise KeyError(f"bond {self.id}: no entry for character {partner_id}")
        value = int(value)
        if not 0 <= value <= BOND_VALUE_MAX:
            raise ValueError(f"bond value must be 0..{BOND_VALUE_MAX}, got {value}")
        struct.pack_into("<H", self._save.buffer, off + 4, value)
        return self

    def set_flag(self, partner_id: int, flag: int) -> "Bond":
        """Set the second u16 of one partner entry (bond event counter)."""
        off = self.entry_offset(partner_id)
        if off is None:
            raise KeyError(f"bond {self.id}: no entry for character {partner_id}")
        flag = int(flag)
        if not 0 <= flag <= 0xFFFF:
            raise ValueError(f"bond flag must be 0..65535, got {flag}")
        struct.pack_into("<H", self._save.buffer, off + 6, flag)
        return self

    def __str__(self) -> str:
        return f"bond#{self.index:3d} @0x{self.file_off:06X} id={self.id} ({len(self.entries)} entries)"


# --------------------------------------------------------------------------- #
# save file
# --------------------------------------------------------------------------- #
class SaveData:
    """Parsed save file.  Edits go into :attr:`buffer`; call :meth:`write`."""

    def __init__(self, path: str, data: bytes):
        if len(data) != FILESIZE:
            raise ValueError(f"{path}: unexpected size {len(data)} "
                             f"(expected {FILESIZE:#x})")
        if data[4:8] != MAGIC:
            raise ValueError(f"{path}: bad magic {data[4:8]!r} (expected {MAGIC!r})")
        self.path = os.path.abspath(path)
        self.buffer = bytearray(data)
        self._parse()

    # -- low level ----------------------------------------------------------
    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.buffer, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.buffer, offset)[0]

    def _w16(self, offset: int, value: int) -> None:
        struct.pack_into("<H", self.buffer, offset, int(value))

    def _w32(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self.buffer, offset, int(value))

    # -- parsing ------------------------------------------------------------
    def _parse(self) -> None:
        b = self.buffer
        y, mo, d, h, mi, se = struct.unpack_from("<HBBBBB", b, HEADER_DATETIME)
        self.header = AttrDict(
            magic="UCSD",
            version=self._u32(HEADER_VERSION),
            file_size=self._u32(HEADER_FILESIZE),
            internal_slot=self._u32(HEADER_SLOT),
            datetime=f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{se:02d}",
            datetime_tuple=(y, mo, d, h, mi, se),
            playtime=self._u32(HEADER_PLAYTIME),
            money=self._u32(HEADER_MONEY),
            renown=self._u32(HEADER_RENOWN),
        )
        self.next_char_id = self._u32(CHR_NEXT_ID)
        self.char_count = self._u32(CHR_COUNT)

        self.characters = []
        for i in range(CHAR_MAX):
            if self._u32(char_offset(i)) == CHAR_TERMINATOR:
                self.terminator = char_offset(i)
                break
            self.characters.append(Character(self, i))
        else:  # pragma: no cover - the array is never full in practice
            raise ValueError("character array has no 0xFFFFFFFF terminator")

        self.items = []
        for slot in range(ITEM_MAX):
            if self._u32(ITEM_BASE + slot * ITEM_STRIDE + ITEM_INDEX) == 0:
                break
            self.items.append(Item(self, slot))

        self.bonds = []
        for i in range(BOND_MAX):
            if self._u32(BOND_BASE + i * BOND_STRIDE) == 0xFFFFFFFF:
                break
            self.bonds.append(Bond(self, i))

        self._char_by_id = None

    # -- lookups ------------------------------------------------------------
    @property
    def char_by_id(self) -> dict:
        if self._char_by_id is None:
            self._char_by_id = {c.id: c for c in self.characters}
        return self._char_by_id

    def _resolve_index(self, index: Any) -> int:
        if isinstance(index, Character):
            return index.index
        index = int(index)
        if not 0 <= index < len(self.characters):
            raise IndexError(f"character index {index} out of range "
                             f"(0..{len(self.characters) - 1})")
        return index

    def get_char(self, ident: Any) -> Character:
        """Look a character up by array index, Character object or character id."""
        if isinstance(ident, Character):
            return ident
        # prefer array index; fall back to character id
        try:
            idx = int(ident)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"bad character identifier: {ident!r}") from exc
        if 0 <= idx < len(self.characters):
            return self.characters[idx]
        if idx in self.char_by_id:
            return self.char_by_id[idx]
        raise KeyError(f"no character with index or id {ident}")

    # -- editing ------------------------------------------------------------
    def set_char(self, index: Any, lv: Optional[int] = None,
                 exp: Optional[int] = None, plus: Optional[Iterable[int]] = None,
                 equipment: Optional[Iterable[int]] = None) -> "SaveData":
        """Edit one character record in the buffer.

        ``index`` may be the array index, a :class:`Character` object or a
        character id.  ``lv`` writes the level *and* the matching cumulative
        EXP total (the game recomputes level from EXP on load); an explicit
        ``exp=`` given together with ``lv=`` is applied afterwards.
        """
        idx = self._resolve_index(index)
        off = char_offset(idx)

        if lv is not None:
            lv = int(lv)
            if not MIN_LEVEL <= lv <= MAX_LEVEL:
                raise ValueError(f"level must be in {MIN_LEVEL}..{MAX_LEVEL}, "
                                 f"got {lv}")
            self._w16(off + CHAR_LV, lv)
            self._w32(off + CHAR_EXP, exp_for_level(lv))

        if exp is not None:
            exp = int(exp)
            if not 0 <= exp <= 0xFFFFFFFF:
                raise ValueError(f"exp out of range: {exp}")
            self._w32(off + CHAR_EXP, exp)

        if plus is not None:
            values = [int(v) for v in plus]
            if len(values) != 10:
                raise ValueError(f"plus needs 10 values, got {len(values)}")
            if any(not 0 <= v <= 255 for v in values):
                raise ValueError("plus values must be 0..255")
            self.buffer[off + CHAR_PLUS:off + CHAR_PLUS + 10] = bytes(values)

        if equipment is not None:
            values = [int(v) for v in equipment]
            if len(values) != 4:
                raise ValueError(f"equipment needs 4 values, got {len(values)}")
            if any(not 0 <= v <= 0xFFFFFFFF for v in values):
                raise ValueError("equipment ids must be 0..0xFFFFFFFF")
            for i, v in enumerate(values):
                self._w32(off + CHAR_EQUIPMENT + 4 * i, v)

        return self

    def set_char_by_id(self, char_id: int, **kwargs: Any) -> "SaveData":
        """Convenience wrapper: :meth:`set_char` looked up by character id."""
        c = self.char_by_id.get(int(char_id))
        if c is None:
            raise KeyError(f"no character with id {char_id}")
        return self.set_char(c.index, **kwargs)

    def set_money(self, value: int) -> "SaveData":
        value = int(value)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"money out of range: {value}")
        self._w32(HEADER_MONEY, value)
        self.header["money"] = value
        return self

    def set_renown(self, value: int) -> "SaveData":
        value = int(value)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"renown out of range: {value}")
        self._w32(HEADER_RENOWN, value)
        self.header["renown"] = value
        return self

    # -- items --------------------------------------------------------------
    def item_by_index(self, inv_index: int) -> Optional[Item]:
        """Look an inventory record up by its ``index`` (what equipment slots store)."""
        inv_index = int(inv_index)
        for it in self.items:
            if it.index == inv_index:
                return it
        return None

    def set_item_count(self, item: Any, count: int) -> "SaveData":
        """Edit the stack count of one inventory record.

        ``item`` may be an :class:`Item`, a slot number (0-based position in
        :attr:`items`) or an inventory ``index`` value.
        """
        if isinstance(item, Item):
            it = item
        elif isinstance(item, int):
            it = self.items[item] if 0 <= item < len(self.items) else None
            if it is None:
                it = self.item_by_index(item)
                if it is None:
                    raise IndexError(f"no item with slot/index {item}")
        else:
            raise TypeError(f"bad item identifier: {item!r}")
        it.count = count
        return self

    def set_item_count_by_id(self, item_id: int, count: int,
                             only_empty: bool = False) -> int:
        """Set the count of every stack of ``item_id``; returns how many changed."""
        changed = 0
        for it in self.items:
            if it.id != int(item_id):
                continue
            if only_empty and it.count:
                continue
            it.count = count
            changed += 1
        return changed

    # -- skills / tactics ---------------------------------------------------
    def set_skill(self, index: Any, slot_idx: int, skill_id: int) -> "SaveData":
        """Replace the skill id of one **occupied** skill slot (experimental).

        Only the first u32 of the slot is written; the category flag, the
        0xFFFF sentinel and the trailing flag stay untouched.  Empty slots are
        refused on purpose: inserting brand-new skills is known to be unsafe.
        """
        ch = self.get_char(index)
        slot_idx = int(slot_idx)
        if not 0 <= slot_idx < SKILL_SLOT_COUNT:
            raise ValueError(f"slot index out of range: {slot_idx}")
        slot = ch.skill_slots[slot_idx]
        if slot["kind"] != "skill":
            raise ValueError("该槽位不是技能条目（空槽或战术槽不可直接写）")
        skill_id = int(skill_id)
        if not 0 <= skill_id <= SKILL_ID_MAX:
            raise ValueError(f"skill id out of range: {skill_id}")
        old = self._u32(slot["file_off"])
        # keep the extra flag bits stored in the high 16 bits of the field
        self._w32(slot["file_off"], (old & 0xFFFF0000) | (skill_id & 0xFFFF))
        return self

    def set_tactic_skill(self, index: Any, slot_idx: int,
                         skill_id: int) -> "SaveData":
        """Replace the skill id of one **occupied** tactic slot (experimental)."""
        ch = self.get_char(index)
        slot_idx = int(slot_idx)
        if not 0 <= slot_idx < SKILL_SLOT_COUNT:
            raise ValueError(f"slot index out of range: {slot_idx}")
        slot = ch.skill_slots[slot_idx]
        if slot["kind"] != "tactic":
            raise ValueError("该槽位不是战术条目")
        skill_id = int(skill_id)
        if not 0 <= skill_id <= SKILL_ID_MAX:
            raise ValueError(f"skill id out of range: {skill_id}")
        self._w32(slot["file_off"] + 4, skill_id)
        return self

    # -- bonds --------------------------------------------------------------
    def bond_for(self, char_id: int) -> Optional[Bond]:
        """Return the bond record whose owning character id is ``char_id``."""
        char_id = int(char_id)
        for b in self.bonds:
            if b.id == char_id:
                return b
        return None

    def set_bond(self, char_id: int, partner_id: int,
                 value: Optional[int] = None, flag: Optional[int] = None) -> "SaveData":
        """Edit one friendship entry (points and/or event counter)."""
        bond = self.bond_for(char_id)
        if bond is None:
            raise KeyError(f"no bond record for character {char_id}")
        if value is not None:
            bond.set_value(partner_id, value)
        if flag is not None:
            bond.set_flag(partner_id, flag)
        return self

    def max_all_bonds(self, value: int = BOND_VALUE_MAX,
                      flag: Optional[int] = None,
                      exclude_ids: Optional[Iterable[int]] = None,
                      owner_ids: Optional[Iterable[int]] = None) -> int:
        """Set every friendship entry to ``value``; returns entries touched.

        ``exclude_ids`` skips partner entries whose character id is listed
        (e.g. generic mercenaries that never take part in story bonds), and
        ``owner_ids`` restricts which bond records are touched.
        """
        excluded = {int(x) for x in exclude_ids} if exclude_ids else set()
        owners = {int(x) for x in owner_ids} if owner_ids is not None else None
        touched = 0
        for bond in self.bonds:
            if owners is not None and bond.id not in owners:
                continue
            for off, other in bond._entry_offsets():
                if other in excluded:
                    continue
                struct.pack_into("<H", self.buffer, off + 4, int(value))
                if flag is not None:
                    struct.pack_into("<H", self.buffer, off + 6, int(flag))
                touched += 1
        return touched

    def max_bonds_for(self, char_id: int, value: int = BOND_VALUE_MAX,
                      flag: Optional[int] = None,
                      exclude_ids: Optional[Iterable[int]] = None) -> int:
        """Set every friendship entry of one character; returns entries touched.

        ``exclude_ids`` skips partner entries whose character id is listed.
        """
        bond = self.bond_for(char_id)
        if bond is None:
            raise KeyError(f"no bond record for character {char_id}")
        excluded = {int(x) for x in exclude_ids} if exclude_ids else set()
        touched = 0
        for off, other in bond._entry_offsets():
            if other in excluded:
                continue
            struct.pack_into("<H", self.buffer, off + 4, int(value))
            if flag is not None:
                struct.pack_into("<H", self.buffer, off + 6, int(flag))
            touched += 1
        return touched

    # -- output -------------------------------------------------------------
    def write(self, path: str, backup: bool = True) -> str:
        """Write the edited buffer to ``path`` and return the absolute path.

        Refuses any path containing a ``data`` directory component (so the
        game's real saves can never be overwritten by mistake), keeps the file
        length at 0x4DF720, and writes a ``<path>.bak`` copy of an existing
        target before overwriting it.
        """
        if len(self.buffer) != FILESIZE:
            raise RuntimeError(f"internal error: buffer size "
                               f"{len(self.buffer)} != {FILESIZE:#x}")
        if self.buffer[4:8] != MAGIC:
            raise RuntimeError("internal error: buffer magic destroyed")
        target = os.path.abspath(os.fspath(path))
        parts = [p for p in target.replace("\\", "/").lower().split("/") if p]
        if "data" in parts:
            raise ValueError("refusing to write inside a 'data' directory "
                             f"(game saves): {target}")
        if os.path.exists(target) and backup:
            shutil.copy2(target, target + ".bak")
        with open(target, "wb") as fh:
            fh.write(bytes(self.buffer))
        self.last_write_path = target
        return target

    def to_bytes(self) -> bytes:
        return bytes(self.buffer)

    # -- diagnostics --------------------------------------------------------
    def verify(self) -> list:
        """Structural sanity check; returns a list of problems (empty = ok)."""
        problems = []
        if len(self.buffer) != FILESIZE:
            problems.append(f"size {len(self.buffer)} != {FILESIZE:#x}")
        if self.buffer[4:8] != MAGIC:
            problems.append("bad magic")
        if self._u32(HEADER_FILESIZE) != FILESIZE:
            problems.append("header file-size field mismatch")
        if self.char_count != len(self.characters):
            problems.append(f"char_count {self.char_count} != "
                            f"{len(self.characters)} records")
        ids = [c.id for c in self.characters]
        if ids != sorted(ids):
            problems.append("character ids not ascending")
        if ids and self.next_char_id < ids[-1]:
            problems.append(f"next_char_id {self.next_char_id} < last id {ids[-1]}")
        for c in self.characters:
            if c.lv != level_for_exp(c.exp):
                problems.append(f"id {c.id}: lv {c.lv} inconsistent with exp "
                                f"{c.exp} (level {level_for_exp(c.exp)})")
        return problems

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (f"<SaveData {os.path.basename(os.path.dirname(self.path))} "
                f"{self.header.datetime} chars={len(self.characters)} "
                f"money={self.header.money}>")


def load_save(path: str) -> SaveData:
    """Load ``path`` and return a :class:`SaveData` (raises on bad format)."""
    with open(path, "rb") as fh:
        return SaveData(path, fh.read())


# --------------------------------------------------------------------------- #
# name tables (produced separately by data-analyst)
# --------------------------------------------------------------------------- #
_NAME_KINDS = ("chars", "classes", "items", "skills")


def _candidate_name_dirs(json_dir: str) -> list:
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [here, os.getcwd(), os.path.dirname(here), os.path.dirname(os.path.dirname(here))]
    candidates = []
    if os.path.isabs(json_dir):
        candidates.append(json_dir)
    else:
        for root in roots:
            candidates.append(os.path.join(root, json_dir))
    return candidates


def load_names(json_dir: str = "names") -> dict:
    """Load ``chars/classes/items/skills`` name tables from ``json_dir``.

    Returns ``{"chars": {id: name}, "classes": {...}, "items": {...},
    "skills": {...}}``.  Missing directory/files, unreadable JSON or odd value
    shapes never raise -- the corresponding table is simply empty, so callers
    can always do ``names["chars"].get(cid)``.
    """
    out = {kind: {} for kind in _NAME_KINDS}
    directory = None
    for cand in _candidate_name_dirs(json_dir):
        if os.path.isdir(cand):
            directory = cand
            break
    if directory is None:
        return out

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith(".json"):
            continue
        kind = os.path.splitext(filename)[0].lower()
        if kind not in out:
            # tolerate files like "chars_zh.json" / "item_names.json"
            for known in _NAME_KINDS:
                if kind.startswith(known) or known in kind:
                    kind = known
                    break
            else:
                continue
        try:
            with open(os.path.join(directory, filename), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            try:
                ident = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, str):
                name = value
            elif isinstance(value, (list, tuple)) and value:
                name = str(value[0])
            elif isinstance(value, dict):
                name = str(value.get("name") or value.get("zh") or value.get("cn")
                            or next(iter(value.values()), ""))
            else:
                name = str(value)
            if name:
                out[kind][ident] = name
    return out


__all__ = [
    "FILESIZE", "MAGIC", "CHAR_BASE", "CHAR_STRIDE", "CHAR_MAX", "ITEM_BASE",
    "ITEM_STRIDE", "BOND_BASE", "BOND_STRIDE", "BOND_ENTRY_SIZE",
    "BOND_VALUE_MAX", "BOND_FLAG_MAX", "ITEM_COUNT_MAX", "TOTAL", "MIN_LEVEL",
    "SKILL_BASE", "SKILL_SLOT_SIZE", "SKILL_SLOT_COUNT", "SKILL_EMPTY",
    "SKILL_ID_MAX", "SKILL_CATEGORY_NAMES",
    "MAX_LEVEL", "PLUS_NAMES", "exp_for_level", "level_for_exp",
    "exp_for_next_level", "char_offset", "load_save", "load_names",
    "SaveData", "Character", "Item", "Bond", "AttrDict",
]
