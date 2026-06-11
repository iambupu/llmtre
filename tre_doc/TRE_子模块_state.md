# TRE 子模块详解：`state`

## 模块定位

**层级**：持久层 — 数据契约与持久化  
**职责**：定义 TRE 的所有 Pydantic 数据模型、状态契约、SQLite 持久化策略，是"数据即法律"原则的核心承载模块。

---

## 目录结构

```
state/
├── __init__.py              # 模块导出
├── models/                  # [核心] Pydantic 数据模型定义
│   ├── __init__.py
│   ├── game_state.py        # 游戏状态（场景、角色、背包等）
│   ├── narrative.py         # 叙事模型（场景快照、记忆）
│   └── session.py           # 会话模型（Session, Turn）
├── definitions/             # 类型定义与常量
│   ├── __init__.py
│   └── enums.py             # 枚举（IntentType, SceneStatus ...）
├── contracts/               # 数据契约（跨模块共享接口定义）
│   ├── __init__.py
│   └── ...
├── data/                    # 种子数据（初始加载的 JSON/YAML）
│   ├── characters.json      # 预设角色
│   ├── items.json           # 预设物品
│   └── locations.json       # 预设地点
├── persistence/             # [核心] 持久化实现
│   ├── database.py          # SQLite 连接管理（Active/Shadow 双表）
│   └── repositories.py     # Repository 模式封装 CRUD
└── tools/                   # 实用工具
    ├── db_initializer.py    # 数据库初始化（建表 + 种子数据导入）
    └── generate_schemas.py  # 从 Pydantic 模型生成 JSON Schema
```

---

## 模型体系（`state/models/`）

### 设计原则：Pydantic v2 BaseModel

所有游戏实体都定义为 Pydantic 模型，确保：
- 严格类型校验（运行时 validation）
- 序列化/反序列化（JSON、dict 互转）
- Schema 生成（`generate_schemas.py`）
- 不可变性保证（`frozen=True` 选项）

### 核心模型

```python
class GameState(BaseModel):
    """全局游戏状态"""
    scene_id: str
    characters: dict[str, Character]
    inventory: list[Item]
    quests: list[Quest]
    world_flags: dict[str, Any]
    timestamp: datetime

class SceneSnapshot(BaseModel):
    """单场景快照"""
    scene_id: str
    location: Location
    present_characters: list[Character]
    recent_memory: str          # MEMORY.md + 会话历史
    available_actions: list[str]
    player_state: CharacterState

class Character(BaseModel):
    id: str
    name: str
    stats: CharacterStats       # HP, MP, STR, DEX ...
    inventory: list[Item]
    location_id: str
    tags: list[str]

class TurnRecord(BaseModel):
    """单回合记录"""
    turn_id: str
    session_id: str
    user_input: str
    nlu_result: dict
    gm_output: dict
    created_at: datetime
```

---

## Active/Shadow 双表快照

### 机制

SQLite 中使用并行表对实现叙事沙盒化和回滚：

```
Active_State   ← 正式"主线"状态
     │
     ├── 正常写入（确定性操作） → 写入 Active
     │
     └── AI 生成剧情 → 写入 Shadow_State
                          │
                    沙盒测试 → 合并回 Active 或丢弃
```

### 实现位置

- `state/persistence/database.py` — 连接池、双表路由
- `game_workflows/scene_rollback.py` — 回滚操作

---

## 持久化层（`state/persistence/`）

### 数据库连接（`database.py`）

```python
class DatabaseManager:
    """SQLite 连接管理器，支持 Active/Shadow 隔离"""
    def get_connection(self, shadow: bool = False) -> sqlite3.Connection:
    def begin_transaction(self):
    def commit(self):
    def rollback(self):
```

### Repository 模式（`repositories.py`）

对每个核心实体提供 Repository 封装：

```python
class SessionRepository:
    def create(self, session: SessionRecord) -> str
    def get(self, session_id: str) -> SessionRecord | None
    def update(self, session: SessionRecord) -> None
    def list_active(self) -> list[SessionRecord]

class TurnRepository:
    def create(self, turn: TurnRecord) -> str
    def get_by_session(self, session_id: str) -> list[TurnRecord]

class GameStateRepository:
    def get_active(self) -> GameState
    def get_shadow(self) -> GameState
    def merge_shadow_to_active(self) -> None
    def discard_shadow(self) -> None
```

---

## 工具脚本

| 文件 | 功能 |
|------|------|
| `db_initializer.py` | 建表 + 从 `data/` 导入种子数据 |
| `generate_schemas.py` | 扫描 models/ 输出 JSON Schema |

### 使用方式

```bash
# 初始化数据库
python state/tools/db_initializer.py

# 模型更改后重新生成 Schema
python state/tools/generate_schemas.py
```

---

## 数据流

```
                    ┌─────────────┐
                    │  Pydantic   │
                    │   Model     │
                    └──────┬──────┘
                           │ validate / dump
                           ▼
              ┌──────────────────────┐
              │  Repository Layer    │
              │  (CRUD + 双表路由)    │
              └──────┬───────────────┘
                     │ SQL
                     ▼
              ┌──────────────────────┐
              │  SQLite (Active/     │
              │  Shadow 双表)        │
              └──────────────────────┘
```

---

## 依赖关系

```
state
  ├── (自包含) — 依赖较少，被其他所有模块依赖
  ├── core/     — EventBus（写操作通过事件总线拦截）
  └── config/   — database path 等配置
```

state 是 TRE 中最底层的基础模块，其他所有模块（agents、game_workflows、web_api、tools）都依赖 state 的数据模型。
