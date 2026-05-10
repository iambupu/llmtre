# TRE 模块架构

> 版本: A2 (Alpha 2)
> 基于: 2026-05-10 代码库
> 目的: 全模块测绘与依赖关系梳理

---

## 1. 总体架构概览

### 四层架构（自底向上）

```
┌──────────────────────────────────────────
│           智能层 (Intelligent Layer)      
│  NLUAgent · GMAgent · ClarifierAgent     
│  演化 Agent · Agent Context              
├──────────────────────────────────────────┤
│           逻辑层 (Logical Layer)          
│  主循环 (LangGraph StateGraph)           
│  事件总线 (EventBus)                      
│  场景快照 (SceneSnapshot)                 
│  确定性工具 (roll/entity/sandbox/task)    
│  外环桥接 (LlamaIndex Workflows)          
│  RAG 只读桥                               
├──────────────────────────────────────────┤
│           持久层 (Persistence Layer)      
│  SQLite (Active/Shadow 双表快照)          
│  Pydantic 模型契约                        
│  种子数据 (JSON)                          
├──────────────────────────────────────────┤
│           资源层 (Resource Layer)         
│  docs/ (规则书输入)                       
│  mods/ (MOD 扩展)                        
│  RAG 向量索引 / 图谱索引                  
│  外部模型配置 (LLM/Embedding)             
└──────────────────────────────────────────┘
```

### 最小回合链路

```
玩家输入
  → Web API (POST /api/sessions/{id}/turns[/stream])
  → 主循环 (game_workflows/main_event_loop.py)
    → NLUAgent 解析自然语言 → 结构化动作
    → 校验/澄清 (validation_helpers)
    → 确定性结算 (resolution_helpers) 
    → 状态写入 (persistence_helpers)
    → 场景推进 (scene_helpers)
    → GMAgent 渲染叙事 (或模板降级)
  → 响应 (JSON / SSE stream events)
  → 外环异步处理 (state_changed / turn_ended / world_evolution)
```

---

## 2. 模块详述

### 2.1 `app.py` — 应用启动入口

| 属性 | 值 |
|------|-----|
| 功能 | Flask 开发服务启动入口 |
| 端口 | 5000 |
| 关键依赖 | `web_api.create_app()` |
| 启动方式 | `python app.py` 或 `uv run python app.py` |

调用 `web_api.create_app()` 创建 Flask 实例，注册所有 Blueprint 后启动。开发调试信息通过 `werkzeug` 日志输出。

---

### 2.2 `web_api/` — Web API 层

**目录结构:**

```
web_api/
├── __init__.py          # Flask app factory: create_app()
├── service.py            # 业务逻辑编排
├── session_store.py      # Web 会话存储 (SQLite + 内存)
└── blueprints/
    ├── sessions.py       # POST /api/sessions
    ├── turns.py          # POST /api/sessions/{id}/turns (普通)
    ├── stream_turns.py   # POST /api/sessions/{id}/turns/stream (SSE)
    ├── memory.py         # 记忆相关 API
    ├── sandbox.py        # 沙盒 commit/discard API
    ├── history.py        # 回合历史 API
    ├── pack.py           # 剧本包 API
    ├── config.py         # 配置查看 API
    ├── playground.py     # /play (legacy) + /app 页面入口
    └── agent_trace.py    # Agent Trace 调试 API
```

**关键文件:**

#### `__init__.py` — Flask App Factory

- `create_app(config_overrides=None)`:
  - 实例化 Flask
  - 加载 `config/default_config.py` + 环境变量覆盖
  - 初始化 SQLite、RAG、事件总线
  - 注册所有 Blueprint (8 个 API Blueprint + 1 个页面 Blueprint)
  - 注册错误处理器
  - 返回 Flask app

#### `session_store.py` — Web 会话存储

- `WebSessionStore` 类:
  - 基于 SQLite + 内存缓存
  - 管理 `web_sessions` 表
  - CRUD: create / load / delete session
  - 支持 session 级别状态缓存
  - 幂等键 (idempotency key) 支持

#### `service.py` — 业务逻辑编排

- 回合处理主入口: 普通和流式路由共用
- 请求体解析 → 幂等性检查 → 主循环调用 → 响应序列化
- SSE 事件流: `received` → `nlu_parsing` → `gm_delta` → `done` / `error`

**Blueprint 职责:**

| Blueprint | 路由 | 功能 |
|-----------|------|------|
| `sessions` | `POST /api/sessions` | 创建新会话 (支持 pack_id) |
| `turns` | `POST /api/sessions/{id}/turns` | 普通回合 (JSON 响应) |
| `stream_turns` | `POST /api/sessions/{id}/turns/stream` | SSE 流式回合 |
| `memory` | `GET/POST /api/sessions/{id}/memory` | 记忆管理 |
| `sandbox` | `POST /api/sessions/{id}/sandbox/commit\|discard` | 沙盒操作 |
| `history` | `GET /api/sessions/{id}/history` | 回合历史 |
| `pack` | `GET /api/packs` | 剧本包列表与元数据 |
| `config` | `GET /api/config` | 配置只读查看 |
| `playground` | `GET /play`, `GET /app` | 前端页面入口 |
| `agent_trace` | `GET /api/sessions/{id}/trace` | Agent 调试 Trace |

---

### 2.3 `core/` — 核心基础设施

```
core/
├── event_bus.py          # 中央事件总线
└── runtime_logging.py    # 运行日志基础设施
```

#### `event_bus.py` — 中央事件总线

- **事件驱动解耦核心**：所有模块间通过事件通信
- `EventBus` 类:
  - 发布/订阅模式
  - 事件类型: `state_changed`, `turn_ended`, `world_evolution`, `mod_hook` 等
  - 支持 `pre_` / `post_` 钩子 (Hook)
  - 钩子优先级排序
  - 写操作冲突检测 (防止并发状态修改)
  - 拦截器机制: 写操作必须通过事件总线事务拦截器
  - 异步投递支持

#### `runtime_logging.py` — 运行日志基础设施

- 统一日志格式
- 主循环节点执行日志
- 事件总线钩子执行日志
- 外环事件投递日志
- 异常堆栈记录
- 工具集成: `python -m tools.logs.check_runtime_logs`

---

### 2.4 `game_workflows/` — 主循环与外环

```
game_workflows/
├── main_event_loop.py            # LangGraph StateGraph 主循环
├── main_loop_config.py           # 主循环配置 / 规则加载
├── main_loop_validation_helpers.py # 动作校验与澄清
├── main_loop_resolution_helpers.py # 确定性结算
├── main_loop_persistence_helpers.py # 状态持久化
├── main_loop_scene_helpers.py    # 场景推进
├── main_loop_outer_helpers.py    # 外环桥接
├── graph_schema.py               # 图模式 / 状态类型
├── event_schemas.py              # 事件模式定义
├── affordances.py                # 可用动作 / 推荐行动
├── async_watchers.py             # 异步观察器
├── rag_readonly_bridge.py        # RAG 只读桥
└── outer_loop_smoke.py           # 外环冒烟测试
```

#### `main_event_loop.py` — LangGraph 主循环

- 使用 `StateGraph` 构建状态机
- 节点:
  1. `parse_input` — 解析用户输入
  2. `nlu_parse` — NLU 自然语言理解
  3. `validate_action` — 动作合法性校验
  4. `clarify_action` — 不明确时发起澄清
  5. `resolve_action` — 确定性结算 (伤害/移动/物品)
  6. `update_state` — 状态写入 SQLite
  7. `advance_scene` — 场景推进
  8. `generate_narrative` — GM 叙事渲染
  9. `emit_turn_events` — 发布回合事件
- 条件边: 根据校验/结算结果分支
- 支持 MOD 钩子插入节点前后

#### `main_loop_config.py` — 分层规则加载

- 规则加载顺序 (后者覆盖前者):
  1. 内置默认规则 `DEFAULT_MAIN_LOOP_RULES`
  2. `config/main_loop_rules.json`
  3. 已启用 MOD 规则覆盖
  4. 剧本规则覆盖 (`LLMTRE_SCENARIO_RULES_PATH`)
  5. 额外规则覆盖 (`LLMTRE_MAIN_LOOP_RULES_EXTRA`)

#### 确定性结算工具集

| 文件 | 功能 |
|------|------|
| `validation_helpers` | 动作合法性校验 (目标是否存在、物品是否持有、位置是否可达) |
| `resolution_helpers` | 数值结算 (攻击 DC 判定、伤害骰、移动消耗、休息恢复) |
| `persistence_helpers` | SQLite Active/Shadow 表写入 |
| `scene_helpers` | 出口/可见对象/可用动作计算、场景推进 |
| `outer_helpers` | 外环事件投递 (Outbox 模式 + 补偿重放) |

#### `rag_readonly_bridge.py` — RAG 只读桥

- 在主循环中读取 RAG 上下文
- 不参与动作合法性、数值结算或状态写入
- 向量索引缺失时降级为空上下文

#### `outer_loop_smoke.py` — 外环冒烟测试

- 验证外环事件投递链路
- 独立运行: `python -m game_workflows.outer_loop_smoke`

---

### 2.5 `agents/` — 智能体层

```
agents/
├── __init__.py
├── agent_context.py       # Agent 上下文管理
├── nlu_agent.py           # 自然语言理解 Agent
├── nlu_schema.py          # NLU 输出 Schema
├── gm_agent.py            # GM 叙事 Agent
├── clarifier_agent.py     # 动作澄清 Agent
└── evolution_agent.py     # 世界演化 Agent
```

#### `nlu_agent.py` — NLU Agent

- 将玩家自然语言解析为结构化动作
- 模式: `rule_first` (当前默认) / `llm_first`
- 输出: `action_type`, `target`, `parameters`
- 降级路径: 规则匹配 → 模板解析 → LLM

#### `gm_agent.py` — GM Agent

- 将结构化回合结果渲染为叙事文本
- 模式: `llm_first` (当前默认) / `deterministic`
- 降级路径: LLM → 叙事模板 (`narrative_templates`)
- 接收 `SceneSnapshot` 作为上下文

#### `clarifier_agent.py` — 澄清 Agent

- 当动作不明确时发起追问
- 例如: "攻击谁？" "使用什么物品？"
- 返回澄清问题列表

#### `evolution_agent.py` — 世界演化 Agent

- 外环异步运行
- 推动 NPC 行为、世界状态变化
- 当前默认关闭

#### `agent_context.py` — Agent 上下文管理

- 管理 `.agent_context/` 目录
- 只读加载 `MEMORY.md`
- 合并到 `SceneSnapshot.recent_memory`
- 只影响叙事上下文，不影响状态写入

---

### 2.6 `state/` — 持久层

```
state/
├── models/                # Pydantic 数据契约
│   ├── __init__.py
│   ├── base.py            # 基类 + 通用字段
│   ├── entity.py          # 实体 (角色/NPC/怪物)
│   ├── action.py          # 动作模型
│   ├── item.py            # 物品模型
│   ├── quest.py           # 任务模型
│   ├── world.py           # 世界/场景模型
│   └── timeline.py        # 时间线模型
├── definitions/           # 类型定义 / Schema
├── contracts/             # API 契约
├── tools/
│   ├── db_initializer.py  # 数据库初始化
│   └── generate_schemas.py # JSON Schema 生成
├── data/
│   ├── entities.json      # 种子实体
│   └── items.json         # 种子物品
└── core_data/
    └── tre_state.db       # SQLite 数据库 (运行时生成)
```

#### Pydantic 模型层级

```
BaseModel (base.py)
├── Entity (entity.py)      # 角色/NPC/怪物基类
│   ├── stats, skills, inventory
│   ├── status_effects, state_flags
│   └── status_summary, status_context
├── Action (action.py)      # 玩家动作
│   ├── action_type, target, parameters
│   └── validation_result
├── Item (item.py)          # 物品
│   ├── item_type, properties
│   └── equip_slot, usage
├── Quest (quest.py)        # 任务
│   ├── objectives, rewards
│   └── progress, state
├── WorldState (world.py)   # 世界状态
│   ├── locations, exits
│   └── time_of_day, weather
└── Timeline (timeline.py)  # 时间线
    └── events, timestamps
```

#### SQLite 双表快照

- `Active_State_*` 表: 主线状态
- `Shadow_State_*` 表: 沙盒分支状态
- 创建会话时复制 Active → Shadow
- `commit`: Shadow → Active 合并
- `discard`: 删除 Shadow 表
- `rollback`: 从 Active 重建 Shadow

---

### 2.7 `tools/` — 确定性工具集

```
tools/
├── roll/                  # 掷骰工具
│   └── ...                # D20 / 伤害骰 / 百分比骰
├── rag/                   # RAG 工具
│   ├── doc_importer.py    # 文档导入
│   ├── main_loop_rag_smoke.py
│   └── main_loop_rag_integration_check.py
├── entity/                # 实体工具
│   └── ...
├── sandbox/               # 沙盒工具
│   └── ...                # Shadow 表管理
├── task/                  # 任务系统
│   └── ...                # AST 白名单表达式求值
├── logs/                  # 日志工具
│   ├── check_runtime_logs.py
│   └── replay_outer_outbox.py
├── mod_manager.py         # MOD 管理器
└── ...
```

**关键工具说明:**

| 工具 | 功能 | 执行方式 |
|------|------|----------|
| `roll/` | D20/伤害/百分比确定性掷骰 | `tools/roll/` |
| `doc_importer.py` | 文档 → RAG 索引 | `python tools/doc_importer.py <path> --group <name> [--sync]` |
| `mod_manager.py` | MOD 扫描与注册 | `python tools/mod_manager.py scan` |
| `db_initializer.py` | 数据库创建与种子数据导入 | `python state/tools/db_initializer.py` |
| `generate_schemas.py` | Pydantic → JSON Schema | `python state/tools/generate_schemas.py` |
| `check_runtime_logs.py` | 运行日志检查 | `python -m tools.logs.check_runtime_logs` |
| `replay_outer_outbox.py` | 外环补偿重放 | `python -m tools.logs.replay_outer_outbox --limit 50` |

---

### 2.8 `config/` — 配置中心

```
config/
├── rag_config.yml               # RAG / LLM / Embedding 配置
├── agent_model_config.yml       # Agent 模型绑定
├── main_loop_rules.json         # 主循环规则 (基础层)
├── mod_registry.yml             # MOD 注册表
├── rag_import_rules.json        # RAG 导入规则
└── default_config.py            # Flask 默认配置
```

| 配置文件 | 核心内容 |
|----------|----------|
| `rag_config.yml` | LLM/Embedding provider、base_url、model、图谱构建开关 |
| `agent_model_config.yml` | profiles.llm / profiles.embedding、bindings.agents.nlu/gm/evolution |
| `main_loop_rules.json` | nlu.keywords、resolution、rag、memory、outer_loop、scene_defaults、narrative_templates |
| `mod_registry.yml` | active_mods[].enabled/priority/conflict_strategy/hooks_manifest |
| `rag_import_rules.json` | 知识库分组、标签、文件路径 |

---

### 2.9 `frontend/` — React 前端

```
frontend/src/
├── api/
│   ├── client.ts          # 统一 API 客户端 (request_id, trace_id)
│   └── sessions.ts       # 会话相关 API
├── components/            # UI 组件
│   ├── SceneCard.tsx      # 场景展示卡片
│   ├── CharacterStatus.tsx # 角色状态
│   ├── InputArea.tsx      # 输入区域
│   ├── DebugPanel.tsx     # 调试面板
│   └── Toolbar.tsx        # 顶部工具栏
├── stores/
│   └── ...                # Zustand UI 状态管理
├── hooks/                 # React Hooks
├── pages/                 # 页面组件
└── App.tsx               # 根组件
```

**技术栈:** React + Vite + TypeScript + TanStack Query + Zustand

**状态边界:**
- TanStack Query: 后端数据与缓存
- Zustand: UI 状态、调试面板、流式临时状态
- React local state: 组件内部输入和展开状态

**数据流:**
- 所有 API 调用通过 `api/client.ts` 统一收口
- SSE 流式读取 → `done` 或 `error` 事件后下结论
- 角色状态来自后端 `active_character.*`，前端只展示不推断

---

### 2.10 `mods/` — MOD 系统

- MOD 目录: `mods/<mod_id>/`
- 元数据文件: `mod_info.json` (名称、版本、作者、依赖)
- 钩子声明: `hooks_manifest`
- 规则覆盖文件 (按顺序检查):
  - `mods/<mod_id>/main_loop_rules.override.json`
  - `mods/<mod_id>/rules/main_loop_rules.override.json`
  - `mods/<mod_id>/rules/main_loop_rules.json`
- 静态数据深度合并: 优先级高的 MOD 覆盖冲突字段
- 动态脚本钩子: 在事件总线 pre/post 钩子中执行
- 注册: `python tools/mod_manager.py scan` → `config/mod_registry.yml`

---

### 2.11 `story_packs/` — 剧本包系统

- v0 使用本地 JSON 文件夹
- 每个 pack 目录包含:
  - `manifest.json` — 包元数据 (名称、描述、版本)
  - `scenes/` — 场景定义
  - `lore/` — 设定资料
- 校验: `python -m tools.packs.validate <pack>`
- 官方 demo: `story_packs/demo_a2_core`
- 非法 pack 不得进入 session 创建列表

---

### 2.12 其他目录

| 目录 | 用途 | 说明 |
|------|------|------|
| `templates/` | Flask 模板 | `app_bootstrap.html` (降级引导), `playground.html` (legacy) |
| `static/` | 静态资源 | `playground.js`, `playground.css` (legacy) |
| `tests/` | pytest 回归测试 | `python -m pytest tests -q` |
| `docs/` | 规则书输入 | .gitignore 忽略，用户自行放入后通过 doc_importer 导入 |
| `knowledge_base/` | RAG 索引 | 向量索引 + 图谱索引 (运行时生成) |
| `.agent_context/` | Agent 上下文 | `AGENTS.md`, `OPS.md`, `MEMORY.md` |
| `.code_md/` | 宏观架构设计 | "法典" 文档 |
| `.coding_docs/` | 微观实现记录 | "施工日志" |

---

## 3. 模块间依赖关系

### 核心调用链

```
web_api/blueprints/*.py
  → web_api/service.py
    → web_api/session_store.py
    → game_workflows/main_event_loop.py
      → agents/nlu_agent.py
      → agents/clarifier_agent.py
      → agents/gm_agent.py
      → game_workflows/main_loop_*.py (validation/resolution/persistence/scene)
      → core/event_bus.py (emit events)
      → game_workflows/rag_readonly_bridge.py
      → game_workflows/main_loop_outer_helpers.py
        → agents/evolution_agent.py (异步)
```

### 依赖矩阵

| 模块 | 依赖 | 被依赖 |
|------|------|--------|
| `core/` | — | 所有模块 |
| `state/` | `core/` | `game_workflows/`, `web_api/`, `tools/` |
| `game_workflows/` | `core/`, `state/`, `agents/`, `tools/` | `web_api/` |
| `agents/` | `core/`, `state/` | `game_workflows/` |
| `web_api/` | `core/`, `state/`, `game_workflows/` | `app.py` |
| `tools/` | `state/`, `config/` | `game_workflows/` |
| `config/` | — | `core/`, `game_workflows/`, `web_api/` |
| `frontend/` | `web_api/` (API) | — |
| `mods/` | `config/` | `game_workflows/` |
| `story_packs/` | — | `web_api/` (session creation) |
| `.agent_context/` | — | `agents/` |

---

## 4. 关键设计决策

| 决策 | 理由 |
|------|------|
| 事件总线解耦 | 所有模块间通过事件通信，避免直接依赖 |
| Active/Shadow 双表 | 沙盒剧情安全回滚，不污染主线 |
| 分层规则覆盖 | 基础配置 → MOD → 剧本 → 环境变量，灵活可扩展 |
| NLU/GM 双模型可降级 | 无模型时仍可走规则/模板路径，保证基本可用 |
| 双路 Web 接口 | SSE 用于流式体验，JSON 用于简单集成 |
| RAG 只读 | 知识库仅作为 Agent 上下文补充，不参与逻辑判定 |
| LLM 禁止计算 | 所有算术/掷骰由确定性工具处理 |
| AST 白名单任务脚本 | 限制表达式可执行范围 |

---

## 5. 数据流与状态管理

### 回合数据流

```
Player Input
  │
  ▼
POST /api/sessions/{id}/turns[/stream]
  │
  ▼
service.create_turn() / create_turn_stream()
  ├─ 幂等性检查 (idempotency key)
  ├─ session_store.load_session()
  ├─ main_event_loop.run(session, input)
  │   ├─ SceneSnapshot (build from DB + MEMORY.md)
  │   ├─ NLUAgent.parse(input) → StructuredAction
  │   ├─ validate_action(action)
  │   ├─ [clarify if needed]
  │   ├─ resolve_action(action) → StateDelta
  │   ├─ persistence_helpers.apply(delta) → SQLite
  │   ├─ scene_helpers.advance() → SceneSnapshot
  │   ├─ GMAgent.render(snapshot) → Narrative
  │   └─ emit_turn_events(snapshot)
  ├─ response (JSON / SSE events)
  └─ outer_loop.enqueue(state_changed, turn_ended)
```

### 状态写入路径

所有写操作路径：
```
  → event_bus.pre_write_hook (冲突检测)
    → SQLite Active/Shadow 表写入
  → event_bus.post_write_hook (通知)
```

---

## 6. 测试与验证

| 命令 | 覆盖范围 |
|------|----------|
| `pytest tests -q` | 回归测试 |
| `ruff check .` | 代码风格 |
| `mypy .` | 类型检查 (生产代码严格, tests/ 排除) |
| `game_workflows.outer_loop_smoke` | 外环投递 |
| `tools.rag.main_loop_rag_smoke` | RAG 读链路 |
| `tools.rag.main_loop_rag_integration_check` | RAG + 主循环集成 |
| `tools.logs.check_runtime_logs` | 运行日志证据 |
| `tools.logs.replay_outer_outbox` | 补偿重放 |
| `tools.packs.validate <pack>` | 剧本包契约校验 |
