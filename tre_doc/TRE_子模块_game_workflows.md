# TRE 子模块详解：`game_workflows`

## 模块定位

**层级**：逻辑层 — 双轨工作流引擎  
**职责**：实现 TRE 核心的"内环同步推理（LangGraph）+ 外环异步演化（LlamaIndex Workflows）"双轨架构，编排 NLU → GM → Response 的主循环管线。

---

## 目录结构

```
game_workflows/
├── __init__.py              # 模块导出
├── helpers/                 # 辅助工具集合
├── main_event_loop.py       # [核心] LangGraph StateGraph 主循环
├── main_loop_config.py      # 主循环规则配置（DEFAULT_MAIN_LOOP_RULES）
├── event_adapter.py         # 事件适配器
├── nlu_dispatch.py          # NLU 分发器
├── outer_loop_bridge.py     # 外环桥梁
├── outer_workflow.py        # LlamaIndex Workflows 外环
├── response_builder.py      # 响应构建器
├── scene_building.py        # 场景构建
├── scene_rollback.py        # 场景回滚（Active/Shadow 表）
└── session_report.py        # 会话报告
```

---

## 内环同步循环（`main_event_loop.py`）

### 架构：LangGraph StateGraph

主循环由 LangGraph 的 `StateGraph` 驱动，以 `MainLoopState` 为状态容器，定义清晰的节点链路：

```
InputState → NLU(parse) → dispatch → SceneBuild → GM(render) 
           → ResponseBuild → handle_actions → OutputState
```

每个节点对应一个处理阶段，通过 `graph.add_node(name, function)` 注册。图结构用 `.add_edge()` / `.add_conditional_edges()` 定义条件路由（例如场景回滚时走回滚分支而非正常渲染）。

### 状态对象：`MainLoopState`

Pydantic 模型定义的全量状态契约，包含：
- `user_input` (str) — 原始用户消息
- `session_id` (str) — 会话标识
- `scene_snapshot` — 当前场景快照
- `nlu_result` — NLU 结构化解析结果
- `gm_output` — GM 渲染输出
- `final_response` — 最终返回给客户端的响应
- `rollback_required` (bool) — 回滚标志
- `error` — 异常上下文

### 执行流程

1. **NLU Parse** — 调用 `agents.NLUAgent.parse()` 将用户输入转为结构化意图
2. **Dispatch** — 根据 NLU 结果分发到对应处理器
3. **Scene Build** — 构建当前场景上下文
4. **GM Render** — 调用 `agents.GMAgent.render()` 生成叙事响应
5. **Response Build** — 构建最终结构化响应
6. **Handle Actions** — 持久化、事件投递等副作用

---

## 外环异步演化（`outer_workflow.py` + `outer_loop_bridge.py`）

### 架构：LlamaIndex Workflows

外环由 `Workflow` 定义异步步骤，`Step` 用 `@step` 装饰器标记。通过 `Workflow.run()` 异步启动，不阻塞主循环。

### 职责

- **世界观演化** — NPC 行为、世界状态、事件经过的渐进变化
- **异步持久化** — 长耗时写入操作不阻塞回合响应
- **后台事件处理** — 定时/条件触发的外环事件（如日夜循环、势力变化）

### 桥接（`outer_loop_bridge.py`）

`OuterLoopBridge` 负责：
1. 将主循环输出翻译为外环工作流可消费的事件格式
2. 管理外环生命周期（启动/暂停/取消）
3. 将外环结果合并回场景快照

---

## 主循环配置（`main_loop_config.py`）

`DEFAULT_MAIN_LOOP_RULES` 字典定义主循环行为开关：

```python
{
    "nlu": {"enabled": True, "mode": "hybrid", "strictness": 0.8},
    "gm": {"enabled": True, "mode": "llm_first", "fallback": "template"},
    "rag": {"read_only_enabled": True, "auto_initialize": False},
    "rollback": {"enabled": True},
    "outer_loop": {"enabled": False},
    "log_level": "INFO"
}
```

这些规则可通过 `main_loop_rules.json` 文件热加载覆盖。

---

## 辅助模块速览

| 文件 | 职责 |
|------|------|
| `event_adapter.py` | 将内部事件转为外环/事件总线兼容格式 |
| `nlu_dispatch.py` | 按 NLU 意图类型路由到不同 GM 通路 |
| `response_builder.py` | 将 GM 输出组装为最终 JSON 响应结构 |
| `scene_building.py` | 从 SQLite 加载场景数据，合并近期记忆 |
| `scene_rollback.py` | 操作 Active/Shadow 双表快照实现回滚 |
| `session_report.py` | 生成会话摘要报告 |
| `helpers/` | 通用辅助函数（时间格式化、文本处理等） |

---

## 双轨协同模式

```
用户输入
  │
  ▼
┌──────────────────────────────────────┐
│  内环同步循环 (LangGraph StateGraph)  │  ← 100-500ms，必须阻塞
│  NLU → SceneBuild → GM → Response   │
│  响应返回客户端                        │
└──────────┬───────────────────────────┘
           │ 外环事件投递
           ▼
┌──────────────────────────────────────┐
│  外环异步演化 (LlamaIndex Workflows)  │  ← 1-30s，不阻塞
│  NPC演化 / 世界推进 / 持久化          │
└──────────────────────────────────────┘
```

**设计原则**：内环保证响应速度，外环保证世界深度。内环输出的关键决策点通过 `event_adapter` 异步投递到外环，外环结果在下一回合通过场景构建合并。

---

## 依赖关系

```
game_workflows
  ├── agents/            # NLUAgent, GMAgent 调用入口
  ├── core/              # EventBus、runtime_logging
  ├── state/             # MainLoopState, SceneSnapshot, SessionRecord
  ├── tools/             # RollTool, SandboxTool 等确定性工具
  └── config/            # main_loop_rules.json, agent_model_config.yml
```
