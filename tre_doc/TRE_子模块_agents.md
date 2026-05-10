# TRE 子模块详解：`agents`

## 模块定位

**层级**：智能层 — 智能体核心  
**职责**：实现 TRE 的四大智能体（NLU、GM、Clarifier、Evolution），负责自然语言理解、叙事生成、歧义澄清和世界演化。

---

## 目录结构

```
agents/
├── __init__.py              # 模块导出
├── nlu_agent.py             # [核心] NLUAgent — 自然语言理解
├── nlu_schema.py            # NLU 结构化输出 schema（Pydantic）
├── gm_agent.py              # [核心] GMAgent — 叙事渲染与游戏主控
├── clarifier_agent.py       # ClarifierAgent — 歧义澄清
├── evolution_agent.py       # EvolutionAgent — 外环世界观演化
└── agent_context.py         # Agent 运行上下文构建
```

---

## NLUAgent（`nlu_agent.py`）

### 职责

将用户的自然语言输入解析为结构化意图，供内环工作流路由。

### 模式：rule_first（确定性优先，LLM 兜底）

```
用户输入
   │
   ├─ 命中确定性规则（关键字/正则匹配）→ 直接返回结构化 NLUResult
   │
   └─ 未命中 → 调用 LLM（通过 agent_model_config 绑定的 LLM profile）
                → LLM 返回解析结果 → 验证 schema → 返回 NLUResult
```

### 核心方法

```python
class NLUAgent:
    async def parse(self, user_input: str, session) -> NLUResult:
        # 1. 确定性规则匹配
        # 2. 若失败 → LLM 调用
        # 3. Schema 验证
        # 4. 返回 NLUResult
```

### NLU Schema（`nlu_schema.py`）

Pydantic 定义的结构化输出：

```python
class NLUResult(BaseModel):
    intent: str              # 意图类型（"query", "action", "dialogue"...）
    entities: dict           # 抽取的实体键值对
    confidence: float        # 置信度 0.0-1.0
    raw_input: str           # 原始输入原文
    clarification_needed: bool  # 是否需澄清
    clarification_question: Optional[str]
```

### 配置控制

通过 `agent_model_config.yml` 控制：

```yaml
bindings:
  agents.nlu:
    enabled: false          # false = 纯确定性模式
    mode: "deterministic"   # deterministic → 跳过 LLM 调用
    llm_profile: null       # null 时只走规则
```

---

## GMAgent（`gm_agent.py`）

### 职责

根据 NLU 结果和场景快照，生成游戏叙事响应（剧情推进、NPC 对话、环境描述等）。

### 模式：llm_first（LLM 优先，模板兜底）

```
场景快照 + NLUResult
   │
   ├─ LLM 可用 → 构建 prompt → LLM 生成叙事文本
   │              → 后处理（结构化、实体校验）
   │
   └─ LLM 不可用 → 模板引擎 → 基于预置模板生成响应
```

### 核心方法

```python
class GMAgent:
    async def render(self, scene_snapshot: SceneSnapshot,
                     nlu_result: NLUResult) -> GMOutput:
        # 1. 构建 GM Prompt（含世界观、角色、历史）
        # 2. LLM 或模板生成
        # 3. 输出结构化 GMOutput
```

### GMOutput

```python
class GMOutput(BaseModel):
    narrative: str           # 叙事文本（Markdown 格式）
    scene_updates: dict      # 场景状态变更
    quest_updates: list      # 任务更新
    suggested_actions: list  # 可选行动列表
    metadata: dict           # 元数据（token 消耗、延迟等）
```

---

## ClarifierAgent（`clarifier_agent.py`）

### 职责

当 NLU 结果 `clarification_needed=True` 时，生成澄清问题引导用户补充意图。

### 工作流

1. 接收低置信度 NLUResult 和歧义候选项
2. 生成一个或多个澄清问题
3. 用户回答后辅助 NLU 重新解析

### 典型场景

- 用户输入过于模糊（"走" → "往哪走？"）
- 意图二义性（"攻击" → "攻击谁？用什么？"）
- 缺少必要实体参数

---

## EvolutionAgent（`evolution_agent.py`）

### 职责

驱动外环的世界世界观演化，包括 NPC 行为调度、世界态势变化、故事事件推进。

### 模式

- **默认关闭**（`evolution.enabled: false`）
- 委托给外环 `outer_workflow.py` 调度
- **禁止 LLM 计算** — 数值变化、状态转移走确定性工具

---

## AgentContext（`agent_context.py`）

### 职责

构建 Agent 执行的运行时上下文，聚合当前会话记忆、场景快照、玩家模板、世界观数据等。

### 输出

```python
class AgentContext(BaseModel):
    session_id: str
    recent_memory: str           # 来自 MEMORY.md + 会话历史
    scene_snapshot: SceneSnapshot
    character_profile: dict
    world_lore: str              # 从 knowledge_base RAG 检索
    game_rules: dict             # 当前生效的规则约束
```

---

## Agent 调度架构

```
主循环 (main_event_loop.py)
  │
  ├──▶ NLUAgent.parse(user_input)          → NLUResult
  │
  ├──▶ [ClarifierAgent.clarify(NLUResult)] → 可选，仅低置信度
  │
  ├──▶ SceneBuilding + AgentContext
  │
  ├──▶ GMAgent.render(scene, nlu)          → GMOutput
  │
  └──▶ EvolutionAgent.evolve()             → 外环异步
```

### 模型配置文件（`agent_model_config.yml`）

控制各 Agent 是否使用真实 LLM：

| Agent | 默认模式 | LLM 绑定 |
|-------|---------|---------|
| NLU | rule_first → LLM fallback | `bindings.agents.nlu` |
| GM | llm_first → template fallback | `bindings.agents.gm` |
| Evolution | disabled | `bindings.agents.evolution` |

纯确定性验收时：将 NLU 和 GM 的 `enabled` 设为 `false`，`mode` 设为 `"deterministic"`，所有 LLM profile 设为 `null`。

---

## 依赖关系

```
agents
  ├── state/           # NLUResult, GMOutput, SceneSnapshot, AgentContext
  ├── core/            # EventBus, runtime_logging
  ├── config/          # agent_model_config.yml
  ├── tools/           # 确定性工具（RollTool 等）
  └── game_workflows/  # 主循环调用入口
```
