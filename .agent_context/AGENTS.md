# TRE 智能体运行时上下文管理规范 (Agent Context Management)

## 1. 上下文边界定义
在 TRE 引擎的运行时中，Agent（智能体）不应感知底层的代码实现逻辑（如 Python 语法、数据库连接池等），而应专注于**叙事状态流转**与**语义意图解析**。

此文档为游戏运行期各业务 Agent 的交互指南，定义 Agent 如何读取、写入与压缩游戏上下文。

`.agent_context/` 的当前实现定位是**本地 Agent 运行期上下文工作区**：
- 规范文件（`AGENTS.md`、`OPS.md`）约束 Agent 的上下文读取、工具调用和错误记录。
- `MEMORY.md` 保存跨会话长期叙事摘要。
- 主循环只读加载 `MEMORY.md`，并把有效内容并入 `SceneSnapshot.recent_memory`，供 NLU、GM 和叙事渲染链路使用。
- 该目录不是 SQLite 状态库，不保存可由 Pydantic/数据库确定查询的事实。

## 2. 动态记忆与上下文层级 (Context Tiers)

为了在极长期的游玩中防止“记忆幻觉”，TRE 的 Agent 理事会必须依赖分层上下文挂载机制，严禁将全量日志一次性注入 Prompt：

### Tier 1: 瞬时工作记忆 (Context Window)
- **挂载方式**：直接注入当前 LLM 的 System/User Prompt。
- **内容限制**：
  - 最近 5-10 轮的玩家对话记录。
  - 当前处于同一场景 (`Location`) 内的活跃 NPC 列表及简要特质。
  - 当前进行中的首要任务 (`Active_Quest`)。

### Tier 2: 剧情摘要池 (Memories)
- **挂载方式**：由 `Evolution Agent` (演化智能体) 定期生成，写入 `.agent_context/MEMORY.md`；运行时由 `agents.agent_context.load_agent_memory()` 只读加载，并在 `MainEventLoop.run()` 中与 Web 会话近期摘要合并。
- **内容规范**：
  - 以精简的条目记录玩家的重大选择和改变（例如：“玩家在铁匠铺杀死了铁匠，导致整个小镇仇恨度升高”）。
  - **触发规则**：GM Agent 需在当前场景发生重大流转（如切换地图、完成任务分支）时，调用工具请求生成新的 Narrative Diff。
- **边界规则**：
  - `MEMORY.md` 只补充叙事背景，不参与动作合法性、数值判定或状态写入。
  - 空章节标题和 HTML 占位注释会被运行时识别为空模板，不会进入 Prompt。
  - Web 会话近期记忆优先于 `.agent_context` 长期记忆，避免长期摘要覆盖当前回合事实。

### Tier 3: 长期结构化事实 (Facts)
- **挂载方式**：存储于持久化层 (SQLite & Pydantic Schema)。
- **交互方式**：Agent **绝对不可**依靠聊天记录来判断玩家当前 HP 或背包中是否有某把钥匙。必须通过意图解析，依赖引擎内部的 MCP (Model Context Protocol) 接口来执行“检索事实”工具。

## 3. RAG 隔离与动态挂载 (Dynamic Lore Injection)
- 游戏世界可能同时加载了多个 MOD，每个 MOD 有独立的地理与历史设定（存储于 `knowledge_base/mods/`）。
- **约束**：Interaction (交互) Agent 在生成叙事时，必须依赖传入的 `MOD_ID` 标签，进行**带作用域的 RAG 检索**。严禁在 A 城市跨域提取 B 城市的无关地理信息。

## 4. 叙事沙盒的上下文分叉 (Sandbox Context Fork)
当玩家进行出格操作（如在主线剧情中突然决定烧毁任务物品），触发了 `Shadow_State` (沙盒机制) 时：
- Agent 的上下文必须被立即**打上沙盒标记** (`[SANDBOX_MODE]`)。
- 在沙盒模式下，GM Agent 应该生成具有极高戏剧张力与实验性的反馈，且应暗示玩家“这种行为可能带来不可控的后果”。
- 一旦玩家选择“虚幻梦境结束”并回滚状态，Agent 必须**清空当前工作记忆**中关于该沙盒的所有交互日志，重置回沙盒开启前的状态锚点。

## 5. Agent 理事会的流转契约
各 Agent 在进行异步通信时，必须携带标准化的上下文包：
- **NLU (意图解析)** 输出的不是一段话，而是纯正的 JSON 结构，将自然语言降维为确定的动机与目标。
- **GM (叙事指挥)** 负责向 **Interaction (交互)** 发送指令，不仅要包含“刚才发生了什么”，还必须包含“当前场景基调 (Tone)”和“待强化的 NPC 特质 (Trait)”。
