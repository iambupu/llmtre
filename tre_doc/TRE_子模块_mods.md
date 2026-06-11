# TRE 子模块详解：`mods`

## 模块定位

**层级**：扩展层 — 模块化修改系统（Modification System）  
**职责**：提供热插拔的 Mod 扩展机制，通过静态数据深度合并和动态脚本钩子（Hook）修改游戏行为，无需改动核心代码。

---

## 目录结构

```
mods/
├── holy_mod/             # "圣光" Mod 示例
│   ├── mod.yaml          # Mod 元数据 + 声明
│   ├── data/             # 静态数据覆写（角色、物品、场景覆盖）
│   │   ├── characters.json
│   │   └── items.json
│   └── hooks/            # 动态脚本钩子
│       ├── on_turn_start.py
│       ├── on_nlu_parse.py
│       └── on_gm_render.py
├── vampire_mod/          # "血族" Mod 示例
│   ├── mod.yaml
│   ├── data/
│   └── hooks/
│       ├── on_gm_render.py
│       └── on_scene_build.py
└── .gitkeep
```

---

## 架构设计

### 双重扩展机制

```
Mod
  ├── 静态数据合并
  │   └── data/*.json → 深度合并到游戏种子数据
  │
  └── 动态脚本钩子
      └── hooks/*.py → 通过 EventBus 按优先级执行
```

### 1. 静态数据合并

Mod 提供 `data/` 目录下的 JSON 文件，与默认游戏数据进行**深度合并**：

- 同名字段：优先级高的 Mod 覆盖优先级低的
- 新增字段：追加到数据中
- 冲突策略：由 `mod_registry.yml` 中的 `conflicts.strategy` 定义

```yaml
# 圣光 Mod 添加的 items.json
{
  "holy_sword": {
    "name": "圣光之剑",
    "damage": 12,
    "effect": "light_heal_on_hit"
  }
}
```

### 2. 动态脚本钩子

Mod 提供 `hooks/` 下的 Python 脚本，通过 `importlib` 动态加载，注册到 `EventBus`：

```python
# mods/holy_mod/hooks/on_gm_render.py
async def hook(context: HookContext):
    """为圣光职业添加特殊叙事渲染"""
    if context.player.get("class") == "paladin":
        context.narrative += "\n\n（圣光在你周围闪耀...）"
    return context
```

### Hook 类型

| Hook 名称 | 触发时机 | 典型用途 |
|-----------|---------|---------|
| `on_turn_start` | 回合开始时 | 初始化状态、条件检查 |
| `on_nlu_parse` | NLU 解析后 | 修改意图识别结果 |
| `on_gm_render` | GM 渲染时 | 注入额外叙事内容 |
| `on_scene_build` | 场景构建时 | 添加 Mod 专属场景元素 |
| `on_response_build` | 响应组装前 | 修改最终返回数据 |

---

## MOD 注册与发现

### 注册流程

1. 开发者在 `mods/` 下创建 Mod 目录（含 `mod.yaml`）
2. 运行 `python tools/mod_manager.py scan`
3. 工具扫描 `mods/` 并更新 `config/mod_registry.yml`
4. 核心在启动时读取 `mod_registry.yml`，加载所有 `enabled: true` 的 Mod

### 优先级与冲突

```yaml
# mod_registry.yml 中的冲突声明
mods:
  holy_mod:
    priority: 10           # 高优先级
  vampire_mod:
    priority: 5            # 低优先级
    conflicts:
      - with: "holy_mod"
        strategy: "overwrite"  # 覆盖策略
```

**优先级排序**：EventBus 在排序 hooks 时按 Mod priority 降序执行（高优先级先执行）。

**冲突策略**：
| 策略 | 行为 |
|------|------|
| `merge` | 深度合并两个 Mod 的数据（默认） |
| `overwrite` | 高优先级覆盖低优先级 |
| `reject` | 拒绝同时加载（启动报错） |

---

## 运行时行为

```
游戏启动
  │
  ├── 读取 mod_registry.yml
  ├── importlib 动态加载启用的 Mod hooks
  ├── 注册到 EventBus（按 priority 排序）
  │
  ▼
运行时事件（如 GM Render）
  │
  ├── EventBus.emit("before_gm_render")
  ├── MOD hooks 按优先级执行
  │   ├── [high] holy_mod.on_gm_render()
  │   └── [low] vampire_mod.on_gm_render()
  ├── 主逻辑（GMAgent.render）
  └── EventBus.emit("after_gm_render")
```

---

## 副作用声明

MOD 钩子必须在 `mod.yaml` 中声明副作用清单：

```yaml
side_effects:
  - modifies: "narrative"
    scope: "append"
  - modifies: "character_stats"
    scope: "read_only"
```

EventBus 根据副作用清单进行**写入冲突检测**，防止两个 Mod 同时修改同一字段导致数据不一致。

---

## 依赖关系

```
mods
  ├── core/     # EventBus（Hook 注册、排序、冲突检测）
  ├── config/   # mod_registry.yml（注册表）
  └── tools/    # mod_manager.py（扫描/注册）
```
