# TRE 子模块详解：`tools`

## 模块定位

**层级**：资源层 — 确定性工具集合  
**职责**：提供 TRE 所有的确定性功能工具，涵盖 RAG、掷骰、沙盒、实体管理、任务系统、SQLite 操作、文档导入、MOD 管理、剧本包验证等。核心原则：**禁止 LLM 计算**，所有数值运算和状态判定由确定性工具完成。

---

## 目录结构

```
tools/
├── doc_importer.py          # 文档导入 RAG
├── mod_manager.py           # MOD 注册管理
├── entity/                  # 实体管理工具
│   └── ...
├── logs/                    # 日志工具
│   └── ...
├── packs/                   # Story Pack 验证工具链
│   ├── __init__.py
│   ├── validate.py          # 剧本包契约校验
│   └── registry.py          # Pack 注册表
├── quest/                   # 任务系统
│   └── ...
├── rag/                     # RAG 检索核心
│   ├── embedder.py          # 向量嵌入
│   ├── retriever.py         # 检索器
│   └── index_manager.py     # 索引管理
├── roll/                    # 掷骰子系统
│   ├── dice.py              # D20/D100/自定义骰子
│   └── probability.py       # 概率计算
├── sandbox/                 # 沙盒工具
│   └── ...
├── sqlite_db/               # SQLite 数据库工具
│   └── ...
└── testing/                 # 测试辅助工具
    └── ...
```

---

## 核心工具详解

### 1. RAG 检索（`tools/rag/`）

**职责**：为 Agent 提供知识库检索能力，支持向量语义搜索和属性图谱查询。

```
tools/rag/
├── embedder.py              # 文本 → 向量嵌入
├── retriever.py             # 混合检索（向量 + 关键字）
└── index_manager.py         # 索引构建、更新、重建
```

**配置**：`config/rag_config.yml` 控制 embedding 模型、chunk 大小、top_k 等。

**文档导入入口**：
```bash
python tools/doc_importer.py <path> --group <name> [--sync]
```

### 2. 掷骰子系统（`tools/roll/`）

**职责**：D20/D100/自定义骰子投掷与概率计算。**严禁 LLM 处理算术运算或掷骰判定。**

```python
# tools/roll/dice.py (示意接口)
class DiceRoller:
    def roll(self, expression: str) -> RollResult:
        """'2d6+3' → {total: 12, rolls: [4, 5], modifier: 3}"""
    def roll_d20(self) -> int:
    def roll_percentage(self) -> int:
```

### 3. 沙盒工具（`tools/sandbox/`）

**职责**：管理 AI 生成内容的沙盒操作，支持隔离执行和回滚。
- 沙盒更改保留在 Shadow 表中直到显式合并
- 提供"试玩预览"和"确认并入主线"两条路径

### 4. 实体管理（`tools/entity/`）

**职责**：角色、物品、地点等游戏实体的查找和操作。
- 通过 Repository 模式访问 SQLite
- 禁止直接写数据库

### 5. 任务系统（`tools/quest/`）

**职责**：管理玩家任务（Quest）的生命周期：接取→进行中→完成/失败。
- 任务定义在 `state/models/` 中
- 任务推进通过事件总线 hook 检测

### 6. Story Pack 验证（`tools/packs/`）

**职责**：对 `story_packs/` 下的剧本包进行契约校验。

```python
# tools/packs/validate.py (示意)
def validate_pack(pack_path: str) -> ValidationResult:
    """校验 manifest.json、scenes/*、lore/* 的引用完整性"""
```

命令行入口：
```bash
python -m tools.packs.validate story_packs/demo_a2_core
```

**校验规则**：
- manifest.json 格式正确性
- 场景引用可解析（`scenes/*.json`）
- 起始场景存在
- 编译摘要完整性
- 非法 pack 不进入运行时

### 7. MOD 管理（`tools/mod_manager.py`）

**职责**：扫描 `mods/` 目录并更新 `config/mod_registry.yml`。

```bash
python tools/mod_manager.py scan
```

### 8. SQLite 工具（`tools/sqlite_db/`）

**职责**：SQLite 数据库底层操作工具，供 Repository 层使用。
- 连接池管理
- 迁移辅助（当前较简单）
- 双表路由支持（Active/Shadow）

---

## 工具使用原则

### 禁止 LLM 计算

> 严禁让 LLM 处理算术运算或掷骰判定。必须使用 `tools/roll/` 中的确定性工具。

这条原则是 TRE 架构的核心约束之一，确保：
- **可复现性** — 同一输入 + 同一骰子 → 同一结果
- **可审计性** — 所有数值判定有日志可查
- **安全性** — LLM 幻觉不会影响游戏平衡

### 写操作通过事件总线

所有修改数据库的写操作必须通过 `core.EventBus` 的事务拦截器，确保：
- MOD 钩子可以在写入前/后插入逻辑
- 冲突检测（`smart_merge` / `overwrite` / `reject`）
- 写入审计日志

---

## 依赖关系

```
tools
  ├── state/     # 数据模型（GameState, Character 等）
  ├── core/      # EventBus（写操作拦截）
  ├── config/    # rag_config.yml, mod_registry.yml
  ├── mods/      # MOD 钩子脚本
  └── story_packs/  # Pack 验证目标
```
