# -*- coding: utf-8 -*-
"""
Unicorn Overlord 存档修改器（可视化）
=====================================
离线存档编辑：解析 UCSAVEFILE.DATA，修改角色等级/经验/词条、金钱、名声、道具数量、技能、羁绊，安全写回。

依赖：uo_core.py（同目录，负责存档二进制读写）、names/*.json（中文词表，可选）。
运行：python UOEditor.py
"""

import os
import sys
import json
import time
import shutil
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# 中文词表目录：程序同级 names/ 优先，其次兼容旧的 _analysis/names/
_SHEET_CANDIDATES = [
    os.path.join(APP_DIR, "names"),
    os.path.join(os.path.dirname(APP_DIR), "names"),
    os.path.join(os.path.dirname(APP_DIR), "_analysis", "names"),
]
SHEET_DIR = next((p for p in _SHEET_CANDIDATES if os.path.isdir(p)), _SHEET_CANDIDATES[0])
BACKUP_DIR = os.path.join(APP_DIR, "backups")


def _load_config():
    """读取程序目录下的 config.json（可选）。"""
    for name in ("config.json", "config.example.json"):
        path = os.path.join(APP_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _detect_savedata_root():
    """自动寻找 PS4 移植版的存档目录。

    查找顺序：config.json 的 ``savedata_dir`` → 环境变量 UO_SAVEDATA_DIR →
    程序目录及其上最多 4 级父目录下的 ``data/user/savedata/1/CUSA36381``。
    找不到时返回程序目录下的占位路径，用户仍可用「打开存档…」手动选择文件。
    """
    candidates = []
    cfg_dir = str(_load_config().get("savedata_dir") or "").strip()
    if cfg_dir:
        candidates.append(cfg_dir)
    env = os.environ.get("UO_SAVEDATA_DIR", "").strip()
    if env:
        candidates.append(env)
    here = APP_DIR
    for _ in range(5):
        candidates.append(os.path.join(here, "data", "user", "savedata", "1", "CUSA36381"))
        candidates.append(os.path.join(here, "savedata", "1", "CUSA36381"))
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    return os.path.join(APP_DIR, "data", "user", "savedata", "1", "CUSA36381")


SAVEDATA_ROOT = _detect_savedata_root()
DEFAULT_SAVE = ""   # 不再硬编码某个存档槽，启动时自动打开最近修改的存档
sys.path.insert(0, APP_DIR)
try:
    import uo_core
except Exception as exc:  # pragma: no cover
    uo_core = None
    CORE_IMPORT_ERROR = exc
else:
    CORE_IMPORT_ERROR = None

PLUS_FIELDS = [
    ("hp", "生命上限"), ("atk", "攻击"), ("def", "防御"),
    ("matk", "魔法攻击"), ("mdef", "魔法防御"), ("hit", "命中"),
    ("avo", "回避"), ("crit", "暴击"), ("guard", "格挡"), ("spd", "速度"),
]
PLUS_MAX = 10         # 存档里是 u8；实测游戏内上限 10（写大于 10 会被游戏收回 10）
LEVEL_MAX = 50
ITEM_COUNT_MAX = getattr(uo_core, "ITEM_COUNT_MAX", 999)
BOND_VALUE_MAX = getattr(uo_core, "BOND_VALUE_MAX", 1000)
BOND_FLAG_MAX = getattr(uo_core, "BOND_FLAG_MAX", 3)
BOND_FULL = 900       # 实测游戏内亲密度自然上限（979 条羁绊记录最大值就是 900）


# ----------------------------------------------------------------------------
# 中文词表
# ----------------------------------------------------------------------------
def _load_json_map(fname):
    """容忍多种 JSON 结构：list[{id,cn}] / dict{id:cn} / dict{id:{cn}}。"""
    path = os.path.join(SHEET_DIR, fname)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("id", item.get("index"))
            text = item.get("cn") or item.get("name") or item.get("text") or item.get("zh")
            if idx is None or not text:
                continue
            try:
                out[int(idx)] = str(text)
            except (TypeError, ValueError):
                continue
    elif isinstance(data, dict):
        for key, val in data.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(val, dict):
                val = val.get("cn") or val.get("name") or val.get("text") or ""
            out[idx] = str(val)
    return out


def _load_json_raw(fname):
    """按原样加载 JSON（dict/list），失败返回 {}。"""
    path = os.path.join(SHEET_DIR, fname)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class NameBook:
    def __init__(self):
        self.chars = _load_json_map("chars.json")
        self.classes = _load_json_map("classes.json")
        self.items = _load_json_map("items.json")
        self.skills = _load_json_map("skills.json")
        self.conditions = _load_json_map("conditions.json")
        self.enums = _load_json_raw("enums.json")

    def char(self, name_id, char_id=None):
        if name_id and self.chars.get(name_id):
            return self.chars[name_id]
        if char_id is not None:
            return f"佣兵（ID {char_id}）"
        return "（佣兵/无名）"

    def cls(self, class_id):
        return self.classes.get(class_id) or f"职业#{class_id}"

    def item(self, item_id):
        return self.items.get(item_id) or f"道具#{item_id}"

    def skill(self, skill_id):
        return self.skills.get(int(skill_id)) or f"技能#{int(skill_id)}"

    def cond(self, cond_id):
        return self.conditions.get(int(cond_id)) or f"条件#{int(cond_id)}"

    @staticmethod
    def cond_ids(packed):
        """拆解条件字段：单个条件，或两个 u16 拼接的复合条件 A | B<<16。"""
        packed = int(packed)
        if 0 <= packed <= 202:
            return [packed]
        lo = packed & 0xFFFF
        hi = (packed >> 16) & 0xFFFF
        if lo <= 202 and hi <= 202:
            return [lo, hi]
        return [packed]

    def cond_text(self, packed):
        ids = self.cond_ids(packed)
        if len(ids) == 1 and not 0 <= ids[0] <= 202:
            return f"条件原始值 0x{ids[0]:08X}"
        return " 或 ".join(self.cond(i) for i in ids)

    def skill_category(self, flag):
        cats = (self.enums or {}).get("skill_categories", {})
        try:
            name = cats.get(str(int(flag)))
        except Exception:
            name = None
        return name or getattr(uo_core, "SKILL_CATEGORY_NAMES", {}).get(
            int(flag), f"类别#{int(flag)}")

    def loaded_ok(self):
        return bool(self.chars or self.classes or self.items)


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def human_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600} 小时 {seconds % 3600 // 60:02d} 分"


def fmt_dt(dt):
    try:
        y, mo, d, h, mi, s = dt
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"
    except Exception:
        return str(dt)


def list_save_slots():
    """扫描存档目录，返回 [(显示名, 路径)]。"""
    out = []
    if not os.path.isdir(SAVEDATA_ROOT):
        return out
    for name in sorted(os.listdir(SAVEDATA_ROOT)):
        full = os.path.join(SAVEDATA_ROOT, name)
        if not os.path.isdir(full) or name.endswith(".sce_sys"):
            continue
        path = os.path.join(full, "UCSAVEFILE.DATA")
        if os.path.isfile(path):
            out.append((name, path))
    return out


def quick_slot_info(path):
    """不依赖核心库，快速读标题信息用于下拉框。"""
    try:
        with open(path, "rb") as f:
            b = f.read(0x40)
        y, mo, d, h, mi, s = (int.from_bytes(b[0x14:0x16], "little"),
                              b[0x16], b[0x17], b[0x18], b[0x19], b[0x1A])
        play = int.from_bytes(b[0x1C:0x20], "little")
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}", play
    except Exception:
        return "?", 0


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class EditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Unicorn Overlord 存档修改器")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+40+30" % (min(1320, sw - 80), min(820, sh - 100)))
        self.minsize(1040, 640)

        self.names = NameBook()
        self.save = None             # uo_core.SaveData
        self.save_path = ""
        self.dirty = False
        self.cur_index = None
        self._suppress_events = False

        self._init_style()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._first_load)

    # ---------------- 外观 ----------------
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        base = ("Microsoft YaHei UI", 10)
        self.option_add("*Font", base)
        style.configure("Treeview", font=base, rowheight=26)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", foreground="#666666")
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(10, 8, 10, 4))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="存档槽：").pack(side=tk.LEFT)
        self.slot_var = tk.StringVar()
        self.slot_combo = ttk.Combobox(bar, textvariable=self.slot_var, width=44, state="readonly")
        self.slot_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.slot_combo.bind("<<ComboboxSelected>>", self.on_slot_selected)
        self._refresh_slots()

        ttk.Button(bar, text="打开存档…", command=self.on_open).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="重新载入", command=self.on_reload).pack(side=tk.LEFT, padx=4)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="另存为…", command=self.on_save_as).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="复制到存档槽…", command=self.on_copy_to_slot).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="保存修改", style="Big.TButton",
                   command=self.on_save).pack(side=tk.LEFT, padx=4)

        self.path_var = tk.StringVar(value="（未打开存档）")
        ttk.Label(self, textvariable=self.path_var, style="Sub.TLabel",
                  padding=(12, 0, 12, 4)).pack(side=tk.TOP, fill=tk.X)

    def _build_body(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=4)
        self._build_char_tab()
        self._build_item_tab()
        self._build_skill_tab()
        self._build_bond_tab()
        self._build_info_tab()

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(8, 3)).pack(side=tk.BOTTOM, fill=tk.X)

    # ---------------- 角色页 ----------------
    def _build_char_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  角色  ")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)

        left = ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew")

        search_row = ttk.Frame(left)
        search_row.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Label(search_row, text="搜索：").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh_char_table())
        ttk.Entry(search_row, textvariable=self.search_var, width=24).pack(side=tk.LEFT)
        self.count_label = ttk.Label(search_row, text="", style="Sub.TLabel")
        self.count_label.pack(side=tk.LEFT, padx=10)

        cols = ("idx", "name", "cls", "lv", "exp", "state")
        heads = ("#", "角色", "职业", "等级", "经验", "状态")
        widths = (44, 150, 130, 60, 100, 80)
        wrap = ttk.Frame(left)
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.char_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.char_tree.heading(c, text=h)
            self.char_tree.column(c, width=w, anchor=tk.CENTER if c in ("idx", "lv", "exp", "state") else tk.W)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.char_tree.yview)
        self.char_tree.configure(yscrollcommand=vsb.set)
        self.char_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.char_tree.tag_configure("max", background="#eaf7ea")
        self.char_tree.bind("<<TreeviewSelect>>", self.on_char_selected)

        right = ttk.Frame(tab, padding=(12, 0, 0, 0))
        right.grid(row=0, column=1, sticky="ns")
        self.detail = ttk.Labelframe(right, text=" 角色详情 ", padding=10, width=400)
        self.detail.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.detail.pack_propagate(False)
        self._build_detail_widgets(self.detail)

    def _build_detail_widgets(self, parent):
        self.lbl_name = ttk.Label(parent, text="未选择角色", style="Title.TLabel")
        self.lbl_name.pack(anchor=tk.W)
        self.lbl_sub = ttk.Label(parent, text="", style="Sub.TLabel")
        self.lbl_sub.pack(anchor=tk.W, pady=(0, 8))

        lv_row = ttk.Frame(parent)
        lv_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(lv_row, text="等级：").pack(side=tk.LEFT)
        self.lv_var = tk.IntVar(value=1)
        self.lv_spin = ttk.Spinbox(lv_row, from_=1, to=LEVEL_MAX, width=6,
                                   textvariable=self.lv_var, command=self._on_level_changed)
        self.lv_spin.pack(side=tk.LEFT)
        self.lv_spin.bind("<KeyRelease>", lambda e: self._on_level_changed())
        ttk.Label(lv_row, text=f"  （1 ~ {LEVEL_MAX}）").pack(side=tk.LEFT)
        self.exp_hint = ttk.Label(parent, text="", style="Sub.TLabel")
        self.exp_hint.pack(anchor=tk.W, pady=(0, 8))

        box = ttk.Labelframe(parent, text=" 词条（属性加成，0 ~ 10，10 为游戏内上限） ", padding=8)
        box.pack(fill=tk.X)
        self.plus_vars = []
        for i, (key, label) in enumerate(PLUS_FIELDS):
            r, c = divmod(i, 2)
            ttk.Label(box, text=label + "：", width=10).grid(row=r, column=c * 2, sticky=tk.W, pady=2)
            var = tk.IntVar(value=0)
            sp = ttk.Spinbox(box, from_=0, to=PLUS_MAX, width=4, textvariable=var)
            sp.grid(row=r, column=c * 2 + 1, sticky=tk.W, padx=(0, 14), pady=2)
            self.plus_vars.append(var)

        self.lbl_equip = ttk.Label(parent, text="", style="Sub.TLabel", justify=tk.LEFT)
        self.lbl_equip.pack(anchor=tk.W, pady=(8, 4))

        btns = ttk.Frame(parent)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="应用修改", command=self.apply_char).pack(side=tk.LEFT)
        ttk.Button(btns, text="满级", command=lambda: self._quick_level(LEVEL_MAX)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="词条拉满", command=self._plus_max).pack(side=tk.LEFT, padx=4)

        batch = ttk.Labelframe(parent, text=" 批量操作 ", padding=8)
        batch.pack(fill=tk.X, pady=(10, 0))
        row1 = ttk.Frame(batch)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="把全部角色设为").pack(side=tk.LEFT)
        self.batch_lv = tk.IntVar(value=LEVEL_MAX)
        ttk.Spinbox(row1, from_=1, to=LEVEL_MAX, width=5, textvariable=self.batch_lv).pack(side=tk.LEFT, padx=6)
        ttk.Label(row1, text="级").pack(side=tk.LEFT)
        ttk.Button(row1, text="执行", command=self.apply_all_levels).pack(side=tk.LEFT, padx=8)
        row2 = ttk.Frame(batch)
        row2.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row2, text="全部角色词条拉满", command=self.apply_all_plus).pack(side=tk.LEFT)
        ttk.Label(row2, text=f"（10 个词条各 {PLUS_MAX} 点）", style="Sub.TLabel").pack(side=tk.LEFT, padx=6)

    # ---------------- 道具页 ----------------
    def _build_item_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  道具  ")
        top = ttk.Frame(tab)
        top.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Label(top, text="搜索：").pack(side=tk.LEFT)
        self.item_search = tk.StringVar()
        self.item_search.trace_add("write", lambda *a: self.refresh_item_table())
        ttk.Entry(top, textvariable=self.item_search, width=24).pack(side=tk.LEFT)
        self.item_count_label = ttk.Label(top, text="", style="Sub.TLabel")
        self.item_count_label.pack(side=tk.LEFT, padx=10)

        cols = ("idx", "name", "count")
        heads = ("位置", "道具", "数量")
        widths = (70, 320, 80)
        wrap = ttk.Frame(tab)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.item_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.item_tree.heading(c, text=h)
            self.item_tree.column(c, width=w, anchor=tk.CENTER if c != "name" else tk.W)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.item_tree.yview)
        self.item_tree.configure(yscrollcommand=vsb.set)
        self.item_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.item_tree.bind("<<TreeviewSelect>>", self.on_item_selected)
        self.item_tree.bind("<Double-1>", self.on_item_selected)

        bar = ttk.Labelframe(tab, text=" 修改数量 ", padding=8)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.item_sel_label = ttk.Label(bar, text="（未选择道具）", width=26,
                                        style="Sub.TLabel")
        self.item_sel_label.grid(row=0, column=0, sticky=tk.W)
        ttk.Label(bar, text="数量：").grid(row=0, column=1, sticky=tk.W, padx=(8, 2))
        self.item_qty_var = tk.IntVar(value=1)
        ttk.Spinbox(bar, from_=0, to=ITEM_COUNT_MAX, width=6,
                    textvariable=self.item_qty_var).grid(row=0, column=2, sticky=tk.W)
        ttk.Button(bar, text="应用到选中", command=self.apply_item_count).grid(
            row=0, column=3, padx=(10, 4))
        ttk.Button(bar, text="同种道具全部设为该数量",
                   command=self.apply_item_count_same_id).grid(row=0, column=4, padx=4)
        ttk.Label(bar, text=f"（0 ~ {ITEM_COUNT_MAX}）", style="Sub.TLabel").grid(
            row=0, column=5, padx=(8, 0), sticky=tk.W)

    # ---------------- 技能页 ----------------
    def _build_skill_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  技能  ")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=2)

        left = ttk.Labelframe(tab, text=" 角色（选择要查看的角色） ", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.skill_char_tree = ttk.Treeview(left, columns=("name", "slots"),
                                            show="headings", selectmode="browse")
        for c, h, w in (("name", "角色", 150), ("slots", "技能数", 66)):
            self.skill_char_tree.heading(c, text=h)
            self.skill_char_tree.column(c, width=w,
                                        anchor=tk.W if c == "name" else tk.CENTER)
        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.skill_char_tree.yview)
        self.skill_char_tree.configure(yscrollcommand=vsb.set)
        self.skill_char_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.skill_char_tree.bind("<<TreeviewSelect>>", self.on_skill_char_selected)

        right = ttk.Labelframe(tab, text=" 技能槽 / 战术设置（16 个槽位） ", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.skill_tree = ttk.Treeview(right, columns=("idx", "cat", "content"),
                                       show="headings", selectmode="browse")
        for c, h, w in (("idx", "槽", 44), ("cat", "类别", 110), ("content", "内容", 330)):
            self.skill_tree.heading(c, text=h)
            self.skill_tree.column(c, width=w,
                                   anchor=tk.CENTER if c in ("idx", "cat") else tk.W)
        vsb2 = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.skill_tree.yview)
        self.skill_tree.configure(yscrollcommand=vsb2.set)
        self.skill_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        self.skill_tree.bind("<<TreeviewSelect>>", self.on_skill_selected)

        bar = ttk.Frame(right)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.skill_sel_label = ttk.Label(bar, text="（未选择槽位）", width=20,
                                         style="Sub.TLabel")
        self.skill_sel_label.grid(row=0, column=0, sticky=tk.W)
        ttk.Label(bar, text="改为技能：").grid(row=0, column=1, padx=(8, 2), sticky=tk.W)
        self.skill_new_var = tk.StringVar()
        self.skill_combo = ttk.Combobox(bar, textvariable=self.skill_new_var,
                                        width=26, state="readonly")
        self.skill_combo.grid(row=0, column=2, sticky=tk.W)
        ttk.Button(bar, text="应用", command=self.apply_skill).grid(
            row=0, column=3, padx=(10, 4))

        hint = ttk.Label(right, justify=tk.LEFT, style="Sub.TLabel",
                         text="说明：技能槽是“已学会/已装备”的快照；升级或转职时游戏会按职业"
                              "技能表重算并可能覆盖这里的改动。空槽不可写入（插入新技能不安全）。")
        hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ---------------- 羁绊页 ----------------
    def _build_bond_tab(self):
        tab = ttk.Frame(self.nb, padding=6)
        self.nb.add(tab, text="  羁绊  ")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        left = ttk.Labelframe(tab, text=" 角色（选择要查看/修改的角色） ", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.bond_char_tree = ttk.Treeview(left, columns=("name", "friends"),
                                           show="headings", selectmode="browse")
        for c, h, w in (("name", "角色", 170), ("friends", "羁绊人数", 90)):
            self.bond_char_tree.heading(c, text=h)
            self.bond_char_tree.column(c, width=w,
                                       anchor=tk.W if c == "name" else tk.CENTER)
        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.bond_char_tree.yview)
        self.bond_char_tree.configure(yscrollcommand=vsb.set)
        self.bond_char_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.bond_char_tree.bind("<<TreeviewSelect>>", self.on_bond_char_selected)

        right = ttk.Labelframe(tab, text=" 羁绊对象与亲密度 ", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.bond_tree = ttk.Treeview(right, columns=("name", "value", "flag"),
                                      show="headings", selectmode="browse")
        for c, h, w in (("name", "对象", 170), ("value", "亲密度", 80), ("flag", "羁绊事件", 80)):
            self.bond_tree.heading(c, text=h)
            self.bond_tree.column(c, width=w,
                                  anchor=tk.W if c == "name" else tk.CENTER)
        vsb2 = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.bond_tree.yview)
        self.bond_tree.configure(yscrollcommand=vsb2.set)
        self.bond_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        self.bond_tree.bind("<<TreeviewSelect>>", self.on_bond_partner_selected)

        bar = ttk.Frame(right)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(bar, text="亲密度：").pack(side=tk.LEFT)
        self.bond_value_var = tk.IntVar(value=0)
        ttk.Spinbox(bar, from_=0, to=BOND_VALUE_MAX, width=6,
                    textvariable=self.bond_value_var).pack(side=tk.LEFT)
        ttk.Label(bar, text=f"（0 ~ {BOND_VALUE_MAX}）").pack(side=tk.LEFT)
        ttk.Label(bar, text="   事件：").pack(side=tk.LEFT)
        self.bond_flag_var = tk.IntVar(value=0)
        ttk.Spinbox(bar, from_=0, to=BOND_FLAG_MAX, width=4,
                    textvariable=self.bond_flag_var).pack(side=tk.LEFT)
        ttk.Button(bar, text="应用到选中对象",
                   command=self.apply_bond).pack(side=tk.LEFT, padx=(10, 0))

        bar2 = ttk.Frame(right)
        bar2.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(bar2, text="一键：", style="Sub.TLabel").pack(side=tk.LEFT)
        ttk.Button(bar2, text="本角色对所有人拉满",
                   command=self.max_bonds_for_char).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar2, text="全员相互拉满",
                   command=self.max_bonds_all).pack(side=tk.LEFT)

    # ---------------- 存档信息页 ----------------
    def _build_info_tab(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="  存档信息  ")

        info = ttk.Labelframe(tab, text=" 基本信息 ", padding=12)
        info.pack(side=tk.TOP, fill=tk.X)
        self.lbl_info = ttk.Label(info, text="（未打开存档）", justify=tk.LEFT)
        self.lbl_info.pack(anchor=tk.W)

        edit = ttk.Labelframe(tab, text=" 可修改字段 ", padding=12)
        edit.pack(side=tk.TOP, fill=tk.X, pady=(12, 0))
        ttk.Label(edit, text="金钱：").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.money_var = tk.StringVar(value="0")
        ttk.Entry(edit, textvariable=self.money_var, width=18).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(edit, text="（u32，上限约 42 亿）", style="Sub.TLabel").grid(row=0, column=2, sticky=tk.W, padx=8)
        ttk.Label(edit, text="名声：").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.renown_var = tk.StringVar(value="0")
        ttk.Entry(edit, textvariable=self.renown_var, width=18).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(edit, text="（游戏内上限 5000）", style="Sub.TLabel").grid(row=1, column=2, sticky=tk.W, padx=8)
        ttk.Button(edit, text="应用", command=self.apply_global).grid(row=2, column=1, pady=(8, 0), sticky=tk.W)

        warn = ("提示：本修改器只改离线存档，不改游戏本体。\n"
                "• 保存前会自动把原文件备份到程序目录的 backups\\ 下。\n"
                "• 等级与经验会同时写入（游戏读档时按经验重算等级）。\n"
                "• 改档后建议先在游戏里读取确认，再覆盖原存档位。")
        ttk.Label(tab, text=warn, style="Sub.TLabel", justify=tk.LEFT).pack(side=tk.TOP, anchor=tk.W, pady=(16, 0))

    # ========================================================================
    # 载入 / 刷新
    # ========================================================================
    def _first_load(self):
        if self.save is not None:
            # 已经（被测试脚本或用户）手动载入过，就不要用“最新存档”覆盖
            return
        slots = list_save_slots()
        if slots:
            # 默认选最新修改的槽
            best = max(slots, key=lambda sp: os.path.getmtime(sp[1]))
            self._load_path(best[1])
        elif DEFAULT_SAVE and os.path.exists(DEFAULT_SAVE):
            self._load_path(DEFAULT_SAVE)

    def _refresh_slots(self):
        slots = list_save_slots()
        labels = []
        self._slot_map = {}
        for name, path in slots:
            dt, play = quick_slot_info(path)
            label = f"{name}    {dt}    {human_time(play)}"
            labels.append(label)
            self._slot_map[label] = path
        self.slot_combo["values"] = labels
        return labels

    def on_slot_selected(self, event=None):
        path = self._slot_map.get(self.slot_var.get())
        if path:
            if self.dirty and not messagebox.askyesno("确认", "当前修改尚未保存，确定要切换存档吗？"):
                return
            self._load_path(path)

    def on_open(self):
        path = filedialog.askopenfilename(
            title="选择存档文件",
            initialdir=SAVEDATA_ROOT,
            filetypes=[("Unicorn Overlord 存档", "UCSAVEFILE.DATA"), ("所有文件", "*.*")])
        if path:
            self._load_path(path)

    def on_reload(self):
        if self.save_path:
            if self.dirty and not messagebox.askyesno("确认", "放弃未保存的修改并重新载入？"):
                return
            self._load_path(self.save_path)

    def _load_path(self, path):
        if uo_core is None:
            messagebox.showerror("核心库缺失",
                                 f"无法导入 uo_core.py：\n{CORE_IMPORT_ERROR}")
            return
        try:
            self.save = uo_core.load_save(path)
        except Exception as exc:
            messagebox.showerror("读取失败", f"{path}\n\n{exc}")
            return
        self.save_path = path
        self.dirty = False
        self.cur_index = None
        # 角色的 4 个装备槽存的是「背包记录序号」，要经 items.index 映射成道具 ID
        self.inv_by_index = {it.index: it.id for it in self.save.items}
        # 角色 ID → 中文名（羁绊页用）
        self.name_by_char_id = {ch.id: self.names.char(ch.name_id, ch.id)
                                for ch in self.save.characters}
        # 佣兵（name_id == 0）是自定义 NPC，不参与剧情羁绊，羁绊相关操作一律跳过
        self.merc_ids = {ch.id for ch in self.save.characters if ch.name_id == 0}
        self.path_var.set(path)
        for label, p in getattr(self, "_slot_map", {}).items():
            if os.path.normcase(p) == os.path.normcase(path):
                self.slot_var.set(label)
                break
        self.refresh_char_table()
        self.refresh_item_table()
        self.refresh_skill_char_table()
        self.refresh_skill_table()
        self.refresh_bond_table()
        self.refresh_bond_partner_table()
        choices = self._skill_choices()
        if choices:
            self.skill_combo.configure(values=choices)
        self.refresh_info()
        self.clear_detail()
        self.status_var.set(f"已载入 {os.path.basename(os.path.dirname(path))}："
                            f"{len(self.save.characters)} 名角色 / {len(self.save.items)} 件道具")

    # ---------------- 表格 ----------------
    def refresh_char_table(self):
        if not self.save:
            return
        self.char_tree.delete(*self.char_tree.get_children())
        kw = self.search_var.get().strip().lower()
        shown = 0
        for ch in self.save.characters:
            name = self.names.char(ch.name_id, ch.id)
            cls = self.names.cls(ch.class_id)
            state = "使用中" if getattr(ch, "in_use", True) else "未使用"
            row = (str(ch.index), name, cls, str(ch.lv), str(ch.exp), state)
            if kw and kw not in " ".join(row).lower():
                continue
            tags = ("max",) if ch.lv >= LEVEL_MAX else ()
            self.char_tree.insert("", tk.END, iid=str(ch.index), values=row, tags=tags)
            shown += 1
        self.count_label.config(text=f"共 {len(self.save.characters)} 人，显示 {shown} 人")

    def refresh_item_table(self):
        if not self.save:
            return
        self.item_tree.delete(*self.item_tree.get_children())
        kw = self.item_search.get().strip().lower()
        shown = 0
        for it in self.save.items:
            name = self.names.item(it.id)
            if kw and kw not in str(it.id) and kw not in name.lower():
                continue
            self.item_tree.insert("", tk.END, iid=str(it.index),
                                  values=(it.index, name, it.count))
            shown += 1
        self.item_count_label.config(text=f"共 {len(self.save.items)} 件，显示 {shown} 件")

    # ---------------- 道具：数量修改 ----------------
    def selected_item(self):
        if not self.save:
            return None
        sel = self.item_tree.selection()
        if not sel:
            return None
        try:
            return self.save.item_by_index(int(sel[0]))
        except Exception:
            return None

    def on_item_selected(self, event=None):
        it = self.selected_item()
        if it is None:
            return
        self.item_sel_label.config(
            text=f"{self.names.item(it.id)}（位置 {it.index}）")
        try:
            self.item_qty_var.set(int(it.count))
        except Exception:
            pass

    def apply_item_count(self):
        it = self.selected_item()
        if it is None:
            messagebox.showinfo("提示", "请先在列表里选择一个道具。")
            return
        try:
            qty = int(self.item_qty_var.get())
        except Exception:
            messagebox.showerror("输入错误", "数量必须是整数。")
            return
        qty = max(0, min(ITEM_COUNT_MAX, qty))
        try:
            self.save.set_item_count(it, qty)
        except Exception as exc:
            messagebox.showerror("修改失败", str(exc))
            return
        self.dirty = True
        self.refresh_item_table()
        self.item_tree.selection_set(str(it.index))
        self.status_var.set(f"已修改：{self.names.item(it.id)} 数量 → {qty}（未保存）")

    def apply_item_count_same_id(self):
        it = self.selected_item()
        if it is None:
            messagebox.showinfo("提示", "请先在列表里选择一个道具。")
            return
        try:
            qty = int(self.item_qty_var.get())
        except Exception:
            messagebox.showerror("输入错误", "数量必须是整数。")
            return
        qty = max(0, min(ITEM_COUNT_MAX, qty))
        n = self.save.set_item_count_by_id(it.id, qty)
        self.dirty = True
        self.refresh_item_table()
        self.status_var.set(f"已把 {self.names.item(it.id)} 的全部 {n} 个堆叠设为 {qty}（未保存）")

    # ---------------- 羁绊 ----------------
    def _char_name(self, char_id):
        return getattr(self, "name_by_char_id", {}).get(int(char_id),
                                                        f"角色#{char_id}")

    def refresh_bond_table(self):
        if not self.save:
            return
        self.bond_char_tree.delete(*self.bond_char_tree.get_children())
        merc = getattr(self, "merc_ids", set())
        for ch in self.save.characters:
            if ch.id in merc:      # 佣兵不参与剧情羁绊，不在羁绊页出现
                continue
            bond = self.save.bond_for(ch.id)
            friends = 0
            if bond is not None:
                friends = sum(1 for (pid, v, _f) in bond.entries if v > 0 and pid not in merc)
            self.bond_char_tree.insert(
                "", tk.END, iid=str(ch.index),
                values=(self.names.char(ch.name_id, ch.id), friends))
        if not self.bond_char_tree.selection() and self.bond_char_tree.get_children():
            first = self.bond_char_tree.get_children()[0]
            self.bond_char_tree.selection_set(first)

    def current_bond_char(self):
        if not self.save:
            return None
        sel = self.bond_char_tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if not 0 <= idx < len(self.save.characters):
            return None
        return self.save.characters[idx]

    def on_bond_char_selected(self, event=None):
        self.refresh_bond_partner_table()

    def refresh_bond_partner_table(self):
        self.bond_tree.delete(*self.bond_tree.get_children())
        ch = self.current_bond_char()
        if ch is None:
            return
        bond = self.save.bond_for(ch.id)
        if bond is None:
            return
        # 只显示/可编辑亲密度不为 0 的对象；佣兵（自定义 NPC）不参与剧情羁绊，排除
        merc = getattr(self, "merc_ids", set())
        for pid, value, flag in bond.entries:
            if value <= 0 or pid in merc:
                continue
            self.bond_tree.insert("", tk.END, iid=str(pid),
                                  values=(self._char_name(pid), value, flag))

    def on_bond_partner_selected(self, event=None):
        sel = self.bond_tree.selection()
        if not sel:
            return
        self.bond_value_var.set(int(self.bond_tree.set(sel[0], "value")))
        self.bond_flag_var.set(int(self.bond_tree.set(sel[0], "flag")))

    def apply_bond(self):
        ch = self.current_bond_char()
        sel = self.bond_tree.selection()
        if ch is None or not sel:
            messagebox.showinfo("提示", "请先选择左侧角色，再选择右侧羁绊对象。")
            return
        pid = int(sel[0])
        try:
            value = max(0, min(BOND_VALUE_MAX, int(self.bond_value_var.get())))
            flag = max(0, min(0xFFFF, int(self.bond_flag_var.get())))
        except Exception:
            messagebox.showerror("输入错误", "亲密度/事件必须是整数。")
            return
        try:
            self.save.set_bond(ch.id, pid, value=value, flag=flag)
        except Exception as exc:
            messagebox.showerror("修改失败", str(exc))
            return
        self.dirty = True
        self.refresh_bond_table()
        self.bond_char_tree.selection_set(str(ch.index))
        self.refresh_bond_partner_table()
        self.bond_tree.selection_set(str(pid))
        self.status_var.set(
            f"已修改羁绊：{self.names.char(ch.name_id, ch.id)} ↔ {self._char_name(pid)} "
            f"亲密度 {value}（未保存）")

    def max_bonds_for_char(self):
        ch = self.current_bond_char()
        if ch is None:
            messagebox.showinfo("提示", "请先在左侧选择一个角色。")
            return
        merc = getattr(self, "merc_ids", set())
        if ch.id in merc:
            messagebox.showinfo("提示", "佣兵是自定义 NPC，不参与剧情羁绊，已跳过。")
            return
        bond = self.save.bond_for(ch.id)
        n_total = len([1 for _p, _v, _f in bond.entries if _p not in merc]) if bond else 0
        name = self.names.char(ch.name_id, ch.id)
        if not messagebox.askyesno(
            "确认",
            f"确定把「{name}」对其他全部 {n_total} 名剧情角色的亲密度都设为 {BOND_FULL}？\n"
            f"（列表里亲密度为 0、平时不显示的对象也会一起拉满；佣兵不参与）",
        ):
            return
        n = self.save.max_bonds_for(ch.id, value=BOND_FULL, exclude_ids=merc)
        self.dirty = True
        self.refresh_bond_table()
        self.bond_char_tree.selection_set(str(ch.index))
        self.refresh_bond_partner_table()
        self.status_var.set(
            f"已把「{name}」对全部 {n} 名角色的亲密度设为 {BOND_FULL}（未保存）")

    def max_bonds_all(self):
        if not self.save:
            return
        merc = getattr(self, "merc_ids", set())
        owners = [ch.id for ch in self.save.characters if ch.id not in merc]
        if not messagebox.askyesno(
            "确认",
            f"确定把全部 {len(owners)} 名剧情角色之间的亲密度全部设为 {BOND_FULL}？\n"
            f"（所有剧情角色 × 所有剧情角色，包括目前为 0 的；佣兵不参与）",
        ):
            return
        n = self.save.max_all_bonds(value=BOND_FULL, exclude_ids=merc, owner_ids=owners)
        self.dirty = True
        self.refresh_bond_table()
        self.refresh_bond_partner_table()
        self.status_var.set(
            f"已把全部剧情角色之间的 {n} 个羁绊亲密度设为 {BOND_FULL}（未保存，佣兵已跳过）")

    # ---------------- 技能 / 战术 ----------------
    def _skill_label(self, skill_id):
        if not skill_id:
            return "（空 / 占位）"
        return self.names.skills.get(skill_id) or f"技能#{skill_id}"

    def _skill_choices(self):
        """下拉框文本列表（仅真实技能；0..22 是占位/状态枚举，不提供写入）。"""
        items = sorted(self.names.skills.items())
        return [f"{sid}  {cn}" for sid, cn in items if sid >= 28]

    def _skill_choice_id(self, text):
        try:
            return int(str(text).split(None, 1)[0])
        except Exception:
            return None

    def refresh_skill_char_table(self):
        if not self.save:
            return
        self.skill_char_tree.delete(*self.skill_char_tree.get_children())
        for ch in self.save.characters:
            used = sum(1 for s in ch.skill_slots if s["occupied"])
            self.skill_char_tree.insert("", tk.END, iid=str(ch.index),
                                        values=(self.names.char(ch.name_id, ch.id), used))
        if not self.skill_char_tree.selection() and self.save.characters:
            first = self.save.characters[0].index
            self.skill_char_tree.selection_set(str(first))
            self.refresh_skill_table()

    def current_skill_char(self):
        if not self.save:
            return None
        sel = self.skill_char_tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if not 0 <= idx < len(self.save.characters):
            return None
        return self.save.characters[idx]

    def on_skill_char_selected(self, event=None):
        self.refresh_skill_table()

    def refresh_skill_table(self):
        self.skill_tree.delete(*self.skill_tree.get_children())
        ch = self.current_skill_char()
        if ch is None:
            return
        cat_names = getattr(uo_core, "SKILL_CATEGORY_NAMES", {})
        for slot in ch.skill_slots:
            kind = slot["kind"]
            if kind == "empty":
                continue
            if kind == "skill":
                cat = self.names.skill_category(slot["category"])
                content = self._skill_label(slot["skill_id"])
                extra = slot.get("extra") or 0
                if extra:
                    content += f"（附加标记 0x{extra:04X}）"
            else:
                cat = "战术设置"
                content = (f"{self.names.cond_text(slot['cond_id'])} → "
                           f"{self._skill_label(slot['skill_id'])}"
                           f"（战术槽 {slot['slot_no'] + 1}）")
            self.skill_tree.insert("", tk.END, iid=str(slot["idx"]),
                                   values=(slot["idx"], cat, content))

    def on_skill_selected(self, event=None):
        sel = self.skill_tree.selection()
        ch = self.current_skill_char()
        if not sel or ch is None:
            return
        slot = ch.skill_slots[int(sel[0])]
        self.skill_sel_label.config(text=f"槽 {slot['idx']}：")
        self.skill_new_var.set("")

    def apply_skill(self):
        ch = self.current_skill_char()
        sel = self.skill_tree.selection()
        if ch is None or not sel:
            messagebox.showinfo("提示", "请先选择左侧角色，再选择右侧槽位。")
            return
        skill_id = self._skill_choice_id(self.skill_new_var.get())
        if skill_id is None:
            messagebox.showinfo("提示", "请先从下拉框选择一个技能。")
            return
        slot_idx = int(sel[0])
        slot = ch.skill_slots[slot_idx]
        try:
            if slot["kind"] == "skill":
                self.save.set_skill(ch.index, slot_idx, skill_id)
            elif slot["kind"] == "tactic":
                self.save.set_tactic_skill(ch.index, slot_idx, skill_id)
            else:
                messagebox.showinfo("提示", "空槽不可写入（插入新技能不安全）。")
                return
        except Exception as exc:
            messagebox.showerror("修改失败", str(exc))
            return
        self.dirty = True
        self.refresh_skill_table()
        self.skill_tree.selection_set(str(slot_idx))
        self.status_var.set(f"已把槽 {slot_idx} 改为 {self._skill_label(skill_id)}（未保存）")

    def refresh_info(self):

        if not self.save:
            return
        h = self.save.header
        playtime = getattr(h, "playtime", None)
        if playtime is None:
            playtime = getattr(h, "playtime_s", 0)
        txt = (f"存档时间：{fmt_dt(h.datetime)}\n"
               f"游戏时长：{human_time(playtime)}\n"
               f"内部槽号：0x{h.internal_slot:03X}\n"
               f"角色数量：{len(self.save.characters)}\n"
               f"道具数量：{len(self.save.items)}\n"
               f"文件大小：{len(self.save.buffer):,} 字节")
        self.lbl_info.config(text=txt)
        self.money_var.set(str(h.money))
        self.renown_var.set(str(h.renown))

    # ---------------- 角色详情 ----------------
    def clear_detail(self):
        self.lbl_name.config(text="未选择角色")
        self.lbl_sub.config(text="")
        self.exp_hint.config(text="")
        self.lbl_equip.config(text="")

    def current_char(self):
        if not self.save or self.cur_index is None:
            return None
        return self.save.characters[self.cur_index]

    def equip_name(self, inv_index):
        """装备槽值 = 背包记录序号 → 道具 ID → 中文名。"""
        item_id = getattr(self, "inv_by_index", {}).get(inv_index)
        if item_id is None:
            return f"未知道具（背包#{inv_index}）"
        return self.names.item(item_id)

    def on_char_selected(self, event=None):
        sel = self.char_tree.selection()
        if not sel:
            return
        self.cur_index = int(sel[0])
        self.load_detail()

    def load_detail(self):
        ch = self.current_char()
        if not ch:
            return
        self._suppress_events = True
        try:
            name = self.names.char(ch.name_id, ch.id)
            cls = self.names.cls(ch.class_id)
            self.lbl_name.config(text=name)
            self.lbl_sub.config(text=f"职业：{cls}    角色ID：{ch.id}    记录#{ch.index}")
            self.lv_var.set(int(ch.lv))
            for var, val in zip(self.plus_vars, ch.plus):
                var.set(int(val))
            eq = getattr(ch, "equipment", []) or []
            eq_lines = []
            for i, inv_index in enumerate(eq, 1):
                if inv_index:
                    eq_lines.append(f"装备{i}：{self.equip_name(inv_index)}")
            self.lbl_equip.config(text="\n".join(eq_lines) if eq_lines else "装备：无")
            self._on_level_changed()
        finally:
            self._suppress_events = False

    def _on_level_changed(self):
        if self._suppress_events or not self.save:
            return
        try:
            lv = int(self.lv_var.get())
        except Exception:
            return
        lv = max(1, min(LEVEL_MAX, lv))
        exp = uo_core.exp_for_level(lv)
        nxt = uo_core.exp_for_level(lv + 1) if lv < LEVEL_MAX else None
        hint = f"对应经验：{exp:,}"
        if nxt:
            hint += f"（下一级 {nxt:,}）"
        self.exp_hint.config(text=hint)

    def apply_char(self):
        ch = self.current_char()
        if not ch:
            messagebox.showinfo("提示", "请先在左侧列表选择一个角色。")
            return
        try:
            lv = max(1, min(LEVEL_MAX, int(self.lv_var.get())))
        except ValueError:
            messagebox.showerror("输入错误", "等级必须是数字。")
            return
        plus = [max(0, min(PLUS_MAX, int(v.get()))) for v in self.plus_vars]
        try:
            self.save.set_char(ch.index, lv=lv, plus=plus)
        except Exception as exc:
            messagebox.showerror("修改失败", str(exc))
            return
        self.dirty = True
        self.refresh_char_table()
        self.char_tree.selection_set(str(ch.index))
        self.load_detail()
        self.status_var.set(f"已修改：{self.names.char(ch.name_id, ch.id)} → Lv{lv}"
                            f"（未保存，点击“保存修改”写入文件）")

    def _quick_level(self, lv):
        ch = self.current_char()
        if not ch:
            return
        self.lv_var.set(lv)
        self._on_level_changed()
        self.apply_char()

    def _plus_max(self):
        ch = self.current_char()
        if not ch:
            messagebox.showinfo("提示", "请先在左侧列表选择一个角色。")
            return
        plus = [PLUS_MAX] * len(PLUS_FIELDS)
        self.save.set_char(ch.index, plus=plus)
        for var, val in zip(self.plus_vars, plus):
            var.set(val)
        self.dirty = True
        self.load_detail()
        self.status_var.set(
            f"已把 {self.names.char(ch.name_id, ch.id)} 的 {len(PLUS_FIELDS)} 个词条拉满"
            f"（各 {PLUS_MAX} 点，未保存）"
        )

    def apply_all_plus(self):
        if not self.save:
            return
        n = len(self.save.characters)
        if not messagebox.askyesno(
            "确认",
            f"确定把全部 {n} 名角色的 {len(PLUS_FIELDS)} 个词条都拉满（各 {PLUS_MAX} 点）？",
        ):
            return
        plus = [PLUS_MAX] * len(PLUS_FIELDS)
        for ch in self.save.characters:
            self.save.set_char(ch.index, plus=plus)
        self.dirty = True
        self.refresh_char_table()
        self.load_detail()
        self.status_var.set(
            f"已把全部 {n} 名角色的词条拉满（未保存，点击“保存修改”写入文件）"
        )

    def apply_all_levels(self):
        if not self.save:
            return
        lv = max(1, min(LEVEL_MAX, int(self.batch_lv.get())))
        if not messagebox.askyesno("确认", f"确定把全部 {len(self.save.characters)} 名角色设为 {lv} 级？"):
            return
        for ch in self.save.characters:
            self.save.set_char(ch.index, lv=lv)
        self.dirty = True
        self.refresh_char_table()
        self.load_detail()
        self.status_var.set(f"已把全部角色设为 {lv} 级（未保存）")

    def apply_global(self):
        if not self.save:
            return
        try:
            money = int(str(self.money_var.get()).replace(",", "").strip())
            renown = int(str(self.renown_var.get()).replace(",", "").strip())
        except ValueError:
            messagebox.showerror("输入错误", "金钱/名声必须是整数。")
            return
        if not (0 <= money <= 0xFFFFFFFF):
            messagebox.showerror("数值越界", "金钱超出 u32 范围。")
            return
        self.save.set_money(money)
        self.save.set_renown(renown)
        self.dirty = True
        self.refresh_info()
        self.status_var.set("已修改金钱/名声（未保存）")

    # ========================================================================
    # 保存
    # ========================================================================
    def _backup_original(self, path):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        slot = os.path.basename(os.path.dirname(path)) or "save"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(BACKUP_DIR, f"{slot}_{stamp}_UCSAVEFILE.DATA")
        shutil.copy2(path, dst)
        return dst

    def _write(self, path, quiet=False):
        if not self.save:
            return False
        buf = self.save.buffer
        expected = 0x4DF720
        if len(buf) != expected:
            messagebox.showerror("写入中止", f"缓冲区大小异常：{len(buf)} != {expected}")
            return False
        bak = None
        if os.path.exists(path):
            bak = self._backup_original(path)
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(buf)
            os.replace(tmp, path)
        except Exception as exc:
            messagebox.showerror("写入失败", f"{exc}")
            return False
        self.dirty = False
        if os.path.normcase(path) == os.path.normcase(self.save_path):
            self._refresh_slots()
        msg = f"已写入：\n{path}"
        if bak:
            msg += f"\n\n原文件备份：\n{bak}"
        if quiet:
            self.status_var.set("已保存")
        else:
            messagebox.showinfo("保存成功", msg)
            self.status_var.set("已保存")
        return True

    def on_save(self):
        if not self.save:
            return
        if not self.dirty and not messagebox.askyesno("确认", "没有检测到修改，仍要写回文件吗？"):
            return
        if not messagebox.askyesno("确认保存",
                                   f"将写回：\n{self.save_path}\n\n"
                                   "原文件会先备份到程序目录的 backups\\。继续？"):
            return
        self._write(self.save_path)

    def on_save_as(self):
        if not self.save:
            return
        path = filedialog.asksaveasfilename(
            title="另存为", defaultextension=".DATA",
            initialfile="UCSAVEFILE.DATA",
            initialdir=APP_DIR,
            filetypes=[("存档文件", "*.DATA"), ("所有文件", "*.*")])
        if path:
            self._write(path)

    def on_copy_to_slot(self):
        """把当前内存中的存档写进另一个存档槽目录（用于新建测试槽）。"""
        if not self.save:
            messagebox.showinfo("提示", "请先打开一个存档。")
            return
        existing = [n for n, _ in list_save_slots()]
        win = tk.Toplevel(self)
        win.title("复制到存档槽")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        ttk.Label(win, text="目标存档槽（可输入新目录名，如 SAVEDATA32）",
                  style="Sub.TLabel").pack(padx=14, pady=(14, 4), anchor=tk.W)
        var = tk.StringVar(value="SAVEDATA32")
        combo = ttk.Combobox(win, textvariable=var, width=30,
                             values=existing + ["SAVEDATA32", "SAVEDATA33"])
        combo.pack(padx=14, pady=(0, 8), fill=tk.X)
        ttk.Label(win, text="目标槽已有存档时，会先备份到程序目录的 backups\\。",
                  style="Sub.TLabel").pack(padx=14, anchor=tk.W)

        def do_copy():
            slot = var.get().strip()
            if not slot:
                return
            if ".." in slot or "/" in slot or "\\" in slot:
                messagebox.showerror("名称非法", "存档槽名不能包含路径分隔符。", parent=win)
                return
            dst_dir = os.path.join(SAVEDATA_ROOT, slot)
            dst = os.path.join(dst_dir, "UCSAVEFILE.DATA")
            if os.path.exists(dst) and not messagebox.askyesno(
                    "覆盖确认", f"{slot} 已有存档，覆盖它吗？", parent=win):
                return
            win.destroy()
            os.makedirs(dst_dir, exist_ok=True)
            self._write(dst, quiet=True)
            # 同步元数据（param.bin / icon0.png），让存档列表能正常显示
            src_meta = self._meta_dir(self.save_path)
            dst_meta = self._meta_dir(dst)
            if src_meta and os.path.isdir(src_meta):
                os.makedirs(dst_meta, exist_ok=True)
                for fn in ("param.bin", "icon0.png"):
                    src_file = os.path.join(src_meta, fn)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, os.path.join(dst_meta, fn))
            self._refresh_slots()
            messagebox.showinfo("完成", f"已复制到存档槽：\n{dst}")

        row = ttk.Frame(win)
        row.pack(padx=14, pady=(6, 14), fill=tk.X)
        ttk.Button(row, text="复制", command=do_copy).pack(side=tk.RIGHT)
        ttk.Button(row, text="取消", command=win.destroy).pack(side=tk.RIGHT, padx=6)

    @staticmethod
    def _meta_dir(save_path):
        """UCSAVEFILE.DATA → 同级 <SLOT>.sce_sys 目录。"""
        if not save_path:
            return ""
        d = os.path.dirname(save_path)
        return os.path.join(os.path.dirname(d), os.path.basename(d) + ".sce_sys")

    def on_close(self):
        if uo_core is None:
            self.destroy()
            return
        if self.dirty and not messagebox.askyesno("退出", "有未保存的修改，确定退出吗？"):
            return
        self.destroy()


def main():
    if uo_core is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("核心库缺失", f"无法导入 uo_core.py：\n{CORE_IMPORT_ERROR}")
        root.destroy()
        return 1
    app = EditorApp()
    # 命令行：python UOEditor.py <UCSAVEFILE.DATA>
    for arg in sys.argv[1:]:
        if not arg.startswith("-") and os.path.isfile(arg):
            app._load_path(os.path.abspath(arg))
            break
    if "--selftest" in sys.argv:
        def _stop():
            try:
                if app.save is None:
                    print("[selftest] 未找到存档（可用 --selftest <存档文件> 或设置 UO_SAVEDATA_DIR）")
                else:
                    print(f"[selftest] save={app.save_path}")
                    print(f"[selftest] chars={len(app.save.characters)} items={len(app.save.items)}")
                print(f"[selftest] names loaded: chars={len(app.names.chars)} "
                      f"classes={len(app.names.classes)} items={len(app.names.items)}")
            except Exception:
                traceback.print_exc()
            app.destroy()
        app.after(600, _stop)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
