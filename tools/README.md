# tools/ — 词表生成脚本（可选）

`../names/*.json` 已经生成好并随仓库提供，**正常使用修改器不需要跑这里的脚本**。
只有你想从游戏数据重新生成中文词表时才需要。

## 数据来源

词表来自游戏语音/文本消息表 `MsgSheet/*.fms`（CRI CPK 格式，简体中文在 `Unicorn_CN.CPK` 覆盖包里，
日文在 `Unicorn.CPK`），以及移植版调试导出里的枚举注释（`Debug/_UcEnum_*.inc`）。

`.fms`（FMSB）结构：`0x20` 字节头 → `count`（@0x14）个 8 字节零表 → count 个 NUL 结尾 UTF-8 字符串
→ 16 字节对齐 → 16 字节 `FEOC` 尾。

索引映射（已用 `Debug/_UcEnum_*.inc` 与实际存档逐条验证）：

| 表 | 映射 |
| --- | --- |
| chars | `id → UcCharaList[i]`（CHARASET） |
| classes | `id → UcClassList[id]`（0..73 = CLASSTYPE） |
| skills | `id → UcSkillList[id + 15]` |
| items | `id ≤ 281 → UcItemList[id + 30]`；`282..982 → UcItemList[id + 311]` |
| names | `id → UcNameList[id]` |

> 注意：角色记录里的装备槽存的是**背包记录序号**，不是道具 ID。名字解析要
> `item_id = items_by_index[value].id`，再查 `items[item_id]`。

## 目录约定

把提取出来的文件按下面摆好，再执行脚本：

```
tools/
  _data/extract/MsgSheet/*.fms        # Unicorn.CPK（日文原文）
  _data/extract/Debug/_UcEnum_*.inc   # 移植版的枚举导出（用于验证/条件表）
  fms_cn/MsgSheet/*.fms               # Unicorn_CN.CPK（简体中文覆盖）
```

`tools/_data` 的位置可以用环境变量 `UO_DATA_DIR` 覆盖。

## 脚本

| 脚本 | 作用 |
| --- | --- |
| `gen_names.py` | 生成 `names/chars.json`、`classes.json`、`items.json`、`skills.json`、`names.json`（默认写到 `tools/`，拷到 `names/` 即可） |
| `build_conditions.py` | 从 `UcFactorList.fms` + `_UcEnum_EquipAi.inc` 生成 `conditions.json` / `conditions_desc.json` / `enums.json`（技能战术条件中文名） |
| `align.py` | 枚举 ↔ 消息表的对齐工具（推断 `fms_index = enum_index + k`） |
| `verify_names.py` / `item_check.py` | 校验词表与存档的一致性 |

CPK 解包本身不在本仓库范围内（可参考 `hachi23/unicorn_overlord_pc` 生态里的 CRI CPK 工具）。
