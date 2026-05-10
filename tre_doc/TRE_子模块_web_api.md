# TRE 子模块详解：web_api

## 模块定位

`web_api/` 是 TRE 引擎的**契约层入口**，位于四层架构的「逻辑层」顶部，直接面向 HTTP 客户端（React 前端 / playground）。职责如下：

- 提供 Flask Web 应用工厂，启动时完成运行时初始化
- 注册 8 个 Blueprint，划分功能边界
- 封装统一的服务层（`SessionService`）和持久化存储（`WebSessionStore`）
- 同时提供普通 JSON 和 SSE 流式回合接口
- 负责请求/响应的 `request_id` 幂等、会话隔离和数据校验

## 目录结构

```
web_api/
├── __init__.py            # Flask 应用工厂 create_app()
├── service.py             # SessionService：会话生命周期管理
├── session_store.py       # WebSessionStore：SQLite 持久化
└── blueprints/
    ├── health.py          # GET /api/health
    ├── playground.py      # GET /app, GET /play（页面路由）
    ├── sessions.py        # POST/GET /api/sessions
    ├── story_packs.py     # GET /api/story-packs
    ├── turns.py           # POST /api/sessions/{id}/turns (JSON)
    │                      # POST .../turns/stream (SSE)
    ├── memory.py          # GET /api/sessions/{id}/memory
    ├── sandbox.py         # POST .../sandbox/commit, /discard
    └── runtime.py         # POST /api/runtime/config
```

## 关键实现细节

### 1. 应用工厂（`__init__.py`）

```python
def create_app() -> Flask:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask(__name__, template_folder=..., static_folder=...)
    initialize_runtime(app)      # 初始化 DB、RAG、MOD 注册表
    app.register_blueprint(health_blueprint)
    app.register_blueprint(playground_blueprint)
    app.register_blueprint(sessions_blueprint)
    app.register_blueprint(story_packs_blueprint)
    app.register_blueprint(turns_blueprint)
    app.register_blueprint(memory_blueprint)
    app.register_blueprint(sandbox_blueprint)
    app.register_blueprint(runtime_blueprint)
    return app
```

`initialize_runtime(app)` 在 service.py 中定义，负责：

- 加载分层主循环规则（`load_main_loop_rules`）
- 加载 Agent 模型配置（`load_agent_model_config`）
- 初始化 `StoryPackRegistry`
- 创建 `EventBus` 单例
- 创建 `MainEventLoop` 实例
- 初始化 SQLite 数据库和运行时表

### 2. 服务层（`service.py`）

**SessionService** 是 Web API 的核心编排器，方法包括：

| 方法 | 功能 |
|------|------|
| `create_session(name, pack_id)` | 创建新 Web 会话，绑定角色与剧本 |
| `load_session(session_id)` | 加载已有会话（包括内存/场景） |
| `list_sessions()` | 列出所有会话元数据 |
| `create_turn(session_id, turn_input)` | 普通回合：走主循环 → 返回 TurnResult |
| `create_turn_stream(session_id, turn_input)` | SSE 流式回合：逐步推送阶段事件 |
| `get_turn_history(session_id)` | 回合历史 |
| `get_memory_context(session_id)` | 记忆上下文 |
| `sandbox_commit/discard(session_id)` | 沙盒并入或回滚 |

关键特性：

- **双路回合等价**：`create_turn` 与 `create_turn_stream` 共享相同的请求体解析、memory policy、session lock、幂等缓存和持久化逻辑；仅输出路径不同。
- **幂等键**：每个请求必须携带 `request_id`，后端的 `WebSessionStore` 会检查 `web_idempotency_keys` 表；命中时直接返回缓存响应。
- **快捷动作归一化**：`_normalize_quick_action_semantic_key()` 将同义动作（检查四周/观察周围）合并为 `inspect-surroundings` 等规范键。
- **输入校验**：通过正则表达式 `REQUEST_ID_PATTERN`、`SESSION_ID_PATTERN`、`CHARACTER_ID_PATTERN` 校验 ID 合法性。

### 3. 会话存储（`session_store.py`）

**WebSessionStore** 封装 SQLite 读写，负责：

| 表 | 用途 |
|----|------|
| `web_sessions` | 会话元数据（name, pack_id, character, status） |
| `web_turns` | 回合记录（turn_input, turn_output, trace） |
| `web_session_memory` | 记忆摘要快照 |
| `web_idempotency_keys` | 幂等缓存（scope + session_id + request_id） |
| `web_runtime_config` | 运行期配置持久化 |

### 4. 路由约定

- 所有 API 路由前缀为 `/api/`
- 页面路由：`GET /app`（React SPA，fallback 到 bootstrap）和 `GET /play`（legacy playground）
- Blueprint 不直接调用数据库，统一经过 `SessionService`

## 设计模式

| 模式 | 实现 |
|------|------|
| **工厂方法** | `create_app()` 组装 Flask 实例 |
| **服务层** | `SessionService` 封装主循环调用和会话编排 |
| **存储仓库** | `WebSessionStore` 隔离 SQLite 细节 |
| **幂等缓存** | 写前查 `web_idempotency_keys`，命中直接返回 |
| **SSE 推流** | Flask Response + event stream，逐步推送 `received`、`nlu_delta`、`gm_delta`、`done`、`error` |

## 依赖关系

```
web_api/
  ├── core/event_bus.py          → 事件总线实例
  ├── game_workflows/            → 主循环、规则加载、外环桥接、场景构建
  │   ├── main_event_loop.py
  │   ├── main_loop_config.py
  │   ├── affordances.py
  │   └── async_watchers.py
  ├── state/contracts/turn.py    → TurnRequestContext, TurnTrace 等契约
  ├── state/tools/               → DB 初始化、运行时 schema 工具
  ├── config/agent_model_loader.py
  ├── tools/packs/registry.py    → StoryPackRegistry
  ├── templates/                 → Flask 模板（bootstrap, playground）
  └── frontend/dist/             → 构建产物的同源托管
```

## 四层架构中的位置

```
智能层  ← agents/
逻辑层  ← web_api/  ←←← 对外 HTTP 接口
持久层  ← state/ (SQLite + Pydantic)
资源层  ← mods/, docs/, config/
```

`web_api/` 是逻辑层最外层的 HTTP 适配器，不包含游戏逻辑——真正的回合处理委托给 `game_workflows/main_event_loop.py`。
