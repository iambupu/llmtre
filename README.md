# TRE: Text TRPG Engine

中文版本：`README.md` | English Version: [README_en.md](README_en.md)

## 目录

- [项目概述](#项目概述)
- [核心特性](#核心特性)
- [术语表](#术语表)
- [架构与最小链路](#架构与最小链路)
- [快速开始](#快速开始)
- [运行与游玩](#运行与游玩)
- [常用开发命令](#常用开发命令)
- [配置文件指南](#配置文件指南)
- [API 概览](#api-概览)
- [主要目录与入口](#主要目录与入口)
- [已知限制](#已知限制)
- [贡献指南](#贡献指南)
- [FAQ](#faq)
- [版本信息](#版本信息)

## 项目概述

TRE（Text TRPG Engine）是一个以“确定性逻辑优先”为核心理念的文本 TRPG 引擎。它把数值规则、状态变化和持久化事实固定在代码与 SQLite 中，把自然语言理解和叙事表达限制在 Agent 层，目标是构建一个可连续试玩、可回滚、可验证、可扩展的 AI 跑团引擎骨架。

## 核心特性

- **确定性逻辑优先**：动作合法性、数值结算和状态写入以后端规则与数据库为准。
- **Agent 可降级**：NLU、GM、外环演化可接真实模型，也可降级到规则或模板路径。
- **双路 Web 接口**：同时提供普通 JSON 回合接口和 SSE 流式回合接口。
- **结构化场景快照**：每回合返回地点、出口、可见对象、可用动作和推荐行动。
- **可回滚沙盒**：支持 Active/Shadow 双表快照和 sandbox commit/discard。
- **MOD 与 RAG 扩展**：支持 MOD 分层覆盖与 RAG 只读上下文补充。
- **可观测性内建**：主循环、事件总线、外环、TurnTrace、SSE 事件都可追踪。

## 术语表

- **TRPG**：Tabletop Role-Playing Game，桌面角色扮演游戏。
- **NLU**：Natural Language Understanding，自然语言理解。
- **GM**：Game Master，游戏主持人。
- **RAG**：Retrieval-Augmented Generation，检索增强生成。
- **MOD**：Modification，修改或扩展模块。
- **SSE**：Server-Sent Events，服务器发送事件，用于流式响应。
- **Active/Shadow**：主线状态表与沙盒状态表，用于隔离未并入主线的剧情变更。

## 架构与最小链路

### 四层架构

- **资源层**：`docs/`、`mods/`、RAG 索引和外部模型配置。
- **持久层**：SQLite、Pydantic 契约、Active/Shadow 双表快照。
- **逻辑层**：主循环、事件总线、场景快照、确定性工具、外环桥接。
- **智能层**：NLUAgent、GMAgent、ClarifierAgent、演化 Agent。

### 最小回合链路

1. 玩家输入进入内环主循环。
2. `NLUAgent` 将自然语言解析为结构化动作。
3. 主循环读取角色状态与 `SceneSnapshot`，完成校验、澄清、确定性结算、状态写入与回合推进。
4. `GMAgent` 渲染叙事；模型不可用时走模板或确定性降级路径。
5. Web API 输出普通 JSON 响应或 SSE 流式事件。
6. 外环异步处理 `state_changed`、`turn_ended`、`world_evolution` 等事件。
7. RAG 只读补充上下文，不参与动作合法性、数值结算或状态写入。

## 快速开始

### 1. 环境与依赖

- Python：`3.14+`
- 推荐使用 `uv` 自动创建环境并安装依赖，官方安装说明见 [uv 安装页](https://docs.astral.sh/uv/getting-started/installation/)：

```bash
uv sync
```

- 如果你更习惯传统方式，也可以继续用 `pip` 安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置模型（可选）

- RAG 配置：`config/rag_config.yml`
- Agent 配置：`config/agent_model_config.yml`

当前仓库验证过的本地模型组合为 `ollama/qwen3:8b`（LLM）和 `ollama/bge-m3`（embedding）。

### 3. 初始化状态、知识库与 MOD

如果你已经使用 `uv sync` 创建环境，下面这些命令也可以直接用 `uv run` 执行：

```bash
uv run python state/tools/db_initializer.py
uv run python tools/doc_importer.py docs/ --group core --sync
uv run python tools/mod_manager.py scan
```

说明：

- `docs/` 默认被 `.gitignore` 忽略，需自行放入规则书或设定文档后再导入。
- 如果跳过手动初始化，`uv run python app.py` 启动时也会尝试自动补齐 SQLite 和向量索引。
- 向量索引初始化失败时会记录告警，并降级为无 RAG 只读上下文，Web 服务仍会继续启动。

### 4. 启动服务

```bash
uv run python app.py
```

## 运行与游玩

启动服务后，可使用以下入口：

- 推荐新版前端：`http://localhost:5000/app`
- legacy playground：`http://localhost:5000/play`

前端开发与构建命令需在 `frontend/` 目录执行：

```bash
npm install
npm run dev
npm run build
```

进一步说明：

- 玩家游玩流程见 [PLAY_GUIDE.md](PLAY_GUIDE.md)。
- `/app` 是当前推荐试玩入口；`/play` 保留用于兼容、对照和调试验收。

## 常用开发命令

### 初始化数据库

用于创建或重建 `state/core_data/tre_state.db`，并从 `state/data/` 写入种子数据。

```bash
python state/tools/db_initializer.py
```

常见场景：

- 首次运行项目。
- 数据库缺失或需要重置试玩状态。
- 修改 `state/models/` 或 `state/data/` 后需要重建本地状态库。

### 导入知识库并重建索引

用于把 `docs/` 中的规则书、设定文档或其他资料登记到 `config/rag_import_rules.json`，并在 `--sync` 时重建 `knowledge_base/indices/`。

```bash
python tools/doc_importer.py docs/ --group core --sync
```

常用参数：

- `path`：要导入的文件或目录，例如 `docs/`、`docs/rules.md`
- `--group <name>`：知识库分组名，例如 `core`、`rules`、`mod_xxx`
- `--tags tag1,tag2`：附加标签
- `--desc "说明"`：分组描述
- `--sync`：导入后立即重建向量索引
- `--mineru`：强制按 MinerU 导出目录处理

不带参数运行时，会直接按现有 `config/rag_import_rules.json` 同步索引：

```bash
python tools/doc_importer.py
```

### 扫描并注册 MOD

用于扫描 `mods/` 下包含 `mod_info.json` 的 MOD 目录，并更新 `config/mod_registry.yml`。

```bash
python tools/mod_manager.py scan
```

注册表会记录：

- `enabled`：是否启用该 MOD
- `priority`：加载优先级，数值越高越优先
- `conflict_strategy`：字段冲突处理策略
- `hooks_manifest`：MOD 声明的钩子、触发点与写入字段

### 生成 JSON Schema

用于从 `state/models/` 中的 Pydantic 模型重新生成 JSON Schema。

```bash
python state/tools/generate_schemas.py
```

### 验证 RAG 与外环

```bash
python -m tools.rag.main_loop_rag_smoke
python -m tools.rag.main_loop_rag_integration_check
python -m game_workflows.outer_loop_smoke
```

这些命令用于验证 RAG 读链路、主循环集成和外环事件投递。

### 进行日志验收与补偿重放

```bash
python -m tools.logs.check_runtime_logs
python -m tools.logs.check_runtime_logs --since-minutes 30
python -m tools.logs.replay_outer_outbox --limit 50
```

- `check_runtime_logs`：检查主循环、事件总线、外环是否留下运行证据
- `replay_outer_outbox`：重放外环补偿队列中的待处理事件

## 配置文件指南

### `config/rag_config.yml`

控制 RAG 知识库、LLM、Embedding 和图谱构建。

常改字段：

- `llm.provider` / `llm.model` / `llm.base_url` / `llm.api_key`：RAG 侧 LLM 配置
- `embedding.provider` / `embedding.model` / `embedding.base_url` / `embedding.api_key`：向量化模型配置
- `property_graph.enabled`：是否构建属性图谱
- `property_graph.extraction_prompt`：图谱三元组抽取提示词
- `metadata_extraction.enable_custom_scoring`：是否开启 LLM 自定义重要性打分

本地 Ollama 示例：

```yaml
llm:
  provider: "ollama"
  model: "qwen3:8b"
  base_url: "http://localhost:11434"

embedding:
  provider: "ollama"
  model: "bge-m3"
  base_url: "http://localhost:11434"
```

修改 embedding 或导入规则后，需要重新同步知识库：

```bash
python tools/doc_importer.py --sync
```

### `config/agent_model_config.yml`

控制 NLU、GM、演化等 Agent 是否调用真实模型，以及各自绑定哪个模型 profile。

核心结构：

- `defaults`：Agent 默认启停、模式、超时和重试策略
- `profiles.llm`：可复用的 LLM 连接配置
- `profiles.embedding`：可复用的 embedding 配置
- `bindings.agents.nlu`：NLU 绑定；当前为 `rule_first`
- `bindings.agents.gm`：GM 绑定；当前为 `llm_first`
- `bindings.agents.evolution`：外环演化 Agent 绑定；当前默认关闭

只验证纯确定性主链路时，需要同时关闭 NLU 和 GM 模型：

```yaml
bindings:
  agents.nlu:
    enabled: false
    mode: "deterministic"
    llm_profile: null
  agents.gm:
    enabled: false
    mode: "deterministic"
    llm_profile: null
```

如果 RAG 索引不存在，启动时是否自动初始化由分层规则快照中的 `rag.auto_initialize` 决定。纯确定性验收时可显式关闭：

```json
{
  "rag": {
    "read_only_enabled": false,
    "auto_initialize": false
  }
}
```

### `config/main_loop_rules.json`

主循环规则的基础层配置文件。当前引擎实际使用的是“分层合并后的规则快照”，不是只读取这一份文件。

规则加载顺序（后者覆盖前者）：

1. 内置默认规则 `DEFAULT_MAIN_LOOP_RULES`
2. `config/main_loop_rules.json`
3. 已启用 MOD 规则覆盖
4. 剧本规则覆盖（环境变量 `LLMTRE_SCENARIO_RULES_PATH`）
5. 额外规则覆盖（环境变量 `LLMTRE_MAIN_LOOP_RULES_EXTRA`）

MOD 规则覆盖文件支持以下路径（按顺序检查）：

- `mods/<mod_id>/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.override.json`
- `mods/<mod_id>/rules/main_loop_rules.json`

常改字段：

- `nlu.action_keywords`：动作关键词映射
- `nlu.target_aliases` / `location_aliases` / `item_aliases`：目标、地点、物品别名
- `resolution`：确定性结算规则，例如攻击 DC、伤害骰、移动消耗、休息恢复
- `rag.read_only_enabled`：主循环是否读取 RAG 上下文
- `rag.auto_initialize`：缺失向量索引时是否允许运行时自动初始化 RAG
- `memory.summary_step`：记忆摘要压缩步长
- `memory.summary_context_size`：记忆构建的最大上下文窗口
- `outer_loop`：外环事件投递、补偿重放、超时、世界演化步长
- `scene_defaults`：缺省场景、可用行动、建议行动
- `narrative_templates`：模型不可用时使用的叙事模板

剧本覆盖示例（Windows PowerShell）：

```powershell
$env:LLMTRE_SCENARIO_RULES_PATH = "D:\path\to\scenario_rules.json"
uv run python app.py
```

额外覆盖示例（多个文件）：

```powershell
$env:LLMTRE_MAIN_LOOP_RULES_EXTRA = "D:\a.json;D:\b.json"
uv run python app.py
```

### `.agent_context/`

保存本地 Agent 运行期上下文规范与长期叙事摘要。

核心文件：

- `AGENTS.md`：Agent 上下文分层、读写边界和协作规范
- `OPS.md`：工具调用、数据流和错误记录规范
- `MEMORY.md`：跨会话长期剧情摘要池

运行时，主循环会只读加载 `.agent_context/MEMORY.md`，过滤空模板和占位注释后，与 Web 会话近期记忆合并到 `SceneSnapshot.recent_memory`。该内容只影响 Agent 叙事上下文，不参与动作合法性、数值判定或状态写入。

### `config/rag_import_rules.json`

记录知识库分组、标签和文件路径。通常建议用 `tools/doc_importer.py` 更新，而不是手写。

分组字段含义：

- `group_name`：分组名
- `description`：分组说明
- `tags`：检索标签
- `file_paths`：纳入该分组的文档路径
- `enable_graph`：该分组是否参与图谱构建

### `config/mod_registry.yml`

记录当前扫描到的 MOD 及其启用状态、优先级、冲突策略和钩子清单。通常由 `python tools/mod_manager.py scan` 生成或同步。

常改字段：

- `active_mods[].enabled`：临时启停某个 MOD
- `active_mods[].priority`：调整 MOD 覆盖顺序
- `active_mods[].conflict_strategy`：调整冲突处理策略

## API 概览

- 创建会话：`POST /api/sessions`
- 普通回合：`POST /api/sessions/{session_id}/turns`
- SSE 流式回合：`POST /api/sessions/{session_id}/turns/stream`

## 主要目录与入口

- `agents/`：智能体（NLU、GM、演化等）
- `config/`：RAG、Agent 模型、主循环规则、MOD 注册表等配置
- `core/`：中央事件总线与运行日志基础设施
- `game_workflows/`：主循环、外环桥接、RAG 只读桥和场景辅助逻辑
- `state/`：Pydantic 数据契约、种子数据、SQLite 初始化与运行期 schema
- `tools/`：确定性工具、RAG 导入、MOD 管理、日志验收和补偿重放工具
- `web_api/`：Flask 契约 API、Blueprint 和 `/play` 页面入口
- `mods/`：MOD 扩展与脚本
- `static/`：legacy playground 前端脚本与样式
- `templates/`：Flask 页面模板
- `frontend/`：React + Vite + TypeScript 前端工程，入口为 `/app`
- `tests/`：pytest 回归测试
- `docs/`：本地规则书与设定文档输入目录，默认被 Git 忽略
- `knowledge_base/`：RAG 向量与图谱索引输出目录
- `.agent_context/`：本地 Agent 上下文规范与长期叙事摘要
- `.code_md/`：宏观架构设计文档
- `.coding_docs/`：微观实现记录
- `app.py`：Flask 开发服务启动入口
- `pyproject.toml`：项目元数据、打包配置和 lint/type-check 配置

## 已知限制

- `/app`（React）与 `/play`（legacy）当前并行存在，接口契约一致，但展示层与调试呈现不完全相同。
- `/app` 顶部工具栏与场景卡片当前已去重：`新会话` / `加载` 只保留在顶部工具栏。
- `/app` 角色状态卡以创建或加载会话后的后端返回为准；未建会话前显示占位值 `--`。
- `/app` 角色状态摘要与状态标签由后端 `active_character.status_summary/status_effects/state_flags/status_context` 提供，前端只展示，不推断状态。
- `/app` 调试控制台采用固定“上下结构”：上方为 `状态 / Trace / 日志 / 内存` Tabs，下方为对应功能区。
- A1 页面直接暴露 `并入主线` / `回滚沙盒` 按钮，但普通新会话默认不是沙盒分支。
- 任务脚本判定链路已改为 AST 白名单表达式求值，但该实现仍不是强安全沙箱；生产环境仍需保证脚本来源可信。

## 贡献指南

### 报告问题

- 使用 [GitHub Issues](https://github.com/iambupu/llmtre/issues) 报告 Bug 或建议功能。
- 提供详细描述、复现步骤和环境信息。

### 提交代码

1. Fork 本仓库。
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 提交更改：`git commit -m "Add your feature"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request。

### 代码规范

- 代码风格检查：`python -m ruff check .`
- 类型检查：`python -m mypy .`
- 回归测试：`python -m pytest tests -q`

### 许可证

本项目采用 [GNU GPL v3](LICENSE) 许可证。

## FAQ

### 如何开始开发？

1. 克隆仓库：`git clone https://github.com/iambupu/llmtre.git`
2. 推荐使用 `uv sync` 安装依赖；也可以用 `pip install -r requirements.txt`
3. 初始化数据库：`uv run python state/tools/db_initializer.py`
4. 启动服务：`uv run python app.py`

### 需要什么硬件？

- Python 3.14+
- 若要运行本地模型，建议使用支持 GPU 的设备

### 如何自定义规则？

修改 `config/main_loop_rules.json`，或新增并启用 MOD 规则覆盖。

### 遇到问题怎么办？

先检查运行日志：

```bash
python -m tools.logs.check_runtime_logs
```

## 版本信息

- 当前版本：A2（Alpha 2）
- 更新日志：见 [CHANGELOG.md](CHANGELOG.md)
