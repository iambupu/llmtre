# TRE 子模块详解：`story_packs`

## 模块定位

**层级**：智能层 — 剧本内容体系  
**职责**：提供标准化的剧本包（Story Pack）格式，作为 A2 起的一等内容来源。定义场景、传说、角色预设等叙事资产的封装与加载契约。

---

## 目录结构

```
story_packs/
├── demo_a2_core/           # 官方示例剧本包（A2 核心）
│   ├── manifest.json       # [必需] Pack 元数据声明
│   ├── scenes/             # 场景定义
│   │   ├── opening.json    # 开场场景
│   │   ├── tavern.json     # 酒馆场景
│   │   └── forest.json     # 森林场景
│   ├── lore/               # 世界观传说
│   │   ├── world_history.json
│   │   └── factions.json
│   ├── personas/           # 角色预设
│   │   ├── hero.json
│   │   └── npc_merchant.json
│   └── compile/            # 编译摘要（预处理优化）
│       └── scene_index.json
└── (更多第三方 Pack)
```

---

## Manifest 规范

`manifest.json` 是 Pack 的入口文件，定义元数据和内容引用：

```json
{
  "id": "demo_a2_core",
  "name": "Demo A2 Core",
  "version": "1.0.0",
  "description": "A2 核心示例剧本，包含开场、酒馆和森林场景",
  "author": "TRE Team",
  "entry_scene": "scenes/opening.json",
  "scenes": [
    "scenes/opening.json",
    "scenes/tavern.json",
    "scenes/forest.json"
  ],
  "lore": [
    "lore/world_history.json",
    "lore/factions.json"
  ],
  "personas": [
    "personas/hero.json",
    "personas/npc_merchant.json"
  ],
  "compile_summary": "compile/scene_index.json",
  "required_packs": [],
  "tags": ["core", "demo", "fantasy"]
}
```

---

## 场景规范（`scenes/*.json`）

```json
{
  "scene_id": "tavern",
  "title": "破晓酒馆",
  "description": "一间温暖的乡村酒馆，壁炉里燃烧着火焰...",
  "exits": [
    {"to": "opening", "condition": "离开酒馆"},
    {"to": "forest", "condition": "接受猎人的委托"}
  ],
  "npcs": [
    {"id": "barkeep", "name": "酒馆老板", "dialog": "dialog/tavern_barkeep.json"}
  ],
  "items": [
    {"id": "ale", "name": "麦酒", "effect": "恢复 5 HP"}
  ],
  "triggers": [
    {"event": "player_has_quest:wolf_hunt", "action": "unlock_exit:forest"}
  ]
}
```

### 场景属性

| 字段 | 说明 | 必需 |
|------|------|------|
| `scene_id` | 唯一标识 | ✅ |
| `title` | 场景标题（显示用） | ✅ |
| `description` | 场景描述文本（Markdown） | ✅ |
| `exits` | 可前往的场景列表 | |
| `npcs` | 场景中的 NPC | |
| `items` | 场景中的物品 | |
| `triggers` | 条件触发行为 | |
| `conditions` | 进入该场景的前置条件 | |

---

## 传说规范（`lore/*.json`）

```json
{
  "id": "world_history",
  "title": "世界历史",
  "content": "在远古时代，魔法与凡人共存...",
  "tags": ["history", "magic"],
  "characters_involved": ["ancient_king", "first_mage"],
  "locations": ["ancient_capital"]
}
```

传说数据通过 RAG 索引为 Agent 提供世界观上下文。

---

## 角色预设（`personas/*.json`）

```json
{
  "persona_id": "hero",
  "name": "无名英雄",
  "class": "warrior",
  "stats": {"hp": 100, "mp": 20, "str": 15, "dex": 12, "int": 8},
  "inventory": ["iron_sword", "leather_armor"],
  "backstory": "你是一名流浪剑士，为了寻找失落的圣物而旅行...",
  "tags": ["player_character", "human"]
}
```

---

## Pack 验证

所有剧本包在加载时必须通过契约校验，由 `tools/packs/validate.py` 执行。

### 校验规则

1. **Manifest 完整性** — `id`、`name`、`entry_scene` 等必需字段
2. **场景引用可解析** — manifest 中声明的 `scenes/*.json` 文件必须存在且格式正确
3. **起始场景存在** — `entry_scene` 引用的场景必须在 scenes 列表中
4. **Exit 引用有效** — 场景的 `exits[].to` 必须指向 scenes 中的合法 scene_id
5. **编译摘要完整性** — 如提供 `compile_summary`，其内容必须与实际场景匹配
6. **依赖 Pack 存在** — 如声明 `required_packs`，对应 Pack 必须已注册

### 命令行

```bash
# 验证单个 Pack
python -m tools.packs.validate story_packs/demo_a2_core

# 验证所有已注册 Pack
python -m tools.packs.validate --all
```

### 失败后果

**坏剧本包不得污染运行时**：非法 Pack 只能进入 validator/registry diagnostics，不得进入可创建 session 的列表，也不得写入 `web_sessions`。

---

## Pack 生命周期

```
Pack 开发
   │
   ▼
写入 manifest.json + scenes/* + lore/* + personas/*
   │
   ▼
运行 validator 校验 ✅
   │
   ▼
注册到 registry（tools/packs/registry.py）
   │
   ▼
用户通过 Web UI 选择 Pack → 创建 Session
   │
   ▼
运行时按场景图推进：
  entry_scene → exit → next_scene → ...
```

---

## 数据权威性规则

> 剧本内容不是权威状态源

- lore、persona、prompt、scene summary 只提供**上下文**
- HP、背包、位置、任务推进和状态写入以 **SQLite/Pydantic 契约** 与 **事件总线事务** 为准
- 场景 `exits` 中的 `condition` 只是展示型入口提示，实际判定在 GM + 确定性工具中完成

---

## 依赖关系

```
story_packs
  ├── tools/packs/    # 验证与注册工具
  ├── web_api/        # Pack 列表查询、Session 绑定 API
  ├── core/           # EventBus（场景切换事件）
  └── state/          # Pack 元数据持久化
```
