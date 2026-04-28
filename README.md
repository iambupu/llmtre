# TRE: Text TRPG Engine

`TRE (Text TRPG Engine)` 是一个以“确定性逻辑优先”为核心理念的文本 TRPG 引擎：把“数值与状态的物理法则”固定在代码和 SQLite 中，把“语义理解与叙事表达”限制在智能体层，目标是做一个可回滚、可验证、可扩展的 AI 跑团底座。

## 最小链路概览

- 玩家输入进入内环主循环
- `NLUAgent` 将自然语言降维为结构化动作
- 主循环读取角色状态与 `SceneSnapshot`，完成校验、澄清、确定性结算、状态写入与回合推进
- `GMAgent` 按配置渲染叙事，并在失败时走可诊断的降级路径
- Web API 提供 JSON 与 SSE 流式回合接口
- 外环异步处理 `state_changed / turn_ended / world_evolution` 等事件
- RAG 以只读方式补充上下文，不参与主逻辑判定

## 快速开始

### 1. 环境与依赖

- Python：`3.14+`
- 安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置模型（可选但推荐）

- RAG 配置：`config/rag_config.yml`
- Agent（叙事）配置：`config/agent_model_config.yml`

本仓库当前验证过的本地模型组合为 `ollama/qwen3:8b`（LLM）与 `ollama/bge-m3`（embedding）。

### 3. 初始化状态、知识库与 MOD

```bash
python state/tools/db_initializer.py
python tools/doc_importer.py docs/ --group core --sync
python tools/mod_manager.py scan
```

说明：
- `docs/` 默认在 `.gitignore` 中忽略；请自行放入规则书/设定文档后再导入建索引。
- 若你跳过手动初始化，`python app.py` 启动时也会尝试自动补齐 SQLite 与向量索引；自动索引失败会直接阻断启动并给出报错提示。

### 4. 启动服务并打开页面

```bash
python app.py
```

打开：

```text
http://localhost:5000/play
```

## 工具使用指南

### SQLite 状态库初始化

用于创建或重建 `state/core_data/tre_state.db`，并从 `state/data/` 写入种子数据。

```bash
python state/tools/db_initializer.py
```

常见使用场景：
- 首次运行项目。
- 数据库文件缺失或需要重置试玩状态。
- 修改 `state/models/` 或 `state/data/` 后需要重新生成本地状态库。

### 知识库导入与索引初始化

用于把 `docs/` 中的规则书、设定文档或其他资料登记到 `config/rag_import_rules.json`，并在 `--sync` 时重建 `knowledge_base/indices/`。

```bash
python tools/doc_importer.py docs/ --group core --sync
```

常用参数：
- `path`：要导入的文件或目录，例如 `docs/`、`docs/rules.md`。
- `--group <name>`：写入的知识库分组，例如 `core`、`rules`、`mod_xxx`。
- `--tags tag1,tag2`：给分组附加标签。
- `--desc "说明"`：写入分组描述。
- `--sync`：导入后立即重建向量索引。
- `--mineru`：强制按 MinerU 导出目录处理。

不带参数运行时，会直接按已有 `config/rag_import_rules.json` 同步索引：

```bash
python tools/doc_importer.py
```

### MOD 扫描与注册

用于扫描 `mods/` 下每个包含 `mod_info.json` 的 MOD 目录，并更新 `config/mod_registry.yml`。

```bash
python tools/mod_manager.py scan
```

注册表会记录：
- `enabled`：是否启用该 MOD。
- `priority`：加载优先级，数值越高越优先。
- `conflict_strategy`：字段冲突处理策略。
- `hooks_manifest`：MOD 声明的钩子、触发点与写入字段。

### JSON Schema 生成

用于从 `state/models/` 中的 Pydantic 模型重新生成 JSON Schema。

```bash
python state/tools/generate_schemas.py
```

修改实体模型后应运行该脚本，以保持模型契约与导出的 schema 一致。

### RAG 与外环验证

```bash
python -m tools.rag.main_loop_rag_smoke
python -m tools.rag.main_loop_rag_integration_check
python -m game_workflows.outer_loop_smoke
```

这些命令用于验证 RAG 读链路、主循环集成和外环事件投递。它们依赖数据库、模型配置和索引已经准备好。

### 日志验收与补偿重放

```bash
python -m tools.logs.check_runtime_logs
python -m tools.logs.check_runtime_logs --since-minutes 30
python -m tools.logs.replay_outer_outbox --limit 50
```

`check_runtime_logs` 用于检查主循环、事件总线、外环是否留下了运行证据；`replay_outer_outbox` 用于重放外环补偿队列中的待处理事件。

## 配置文件指南

### `config/rag_config.yml`

控制 RAG 知识库、LLM、Embedding 和图谱构建。

常改字段：
- `llm.provider` / `llm.model` / `llm.base_url` / `llm.api_key`：RAG 侧 LLM 配置，用于生成回答、图谱抽取或自定义打分。
- `embedding.provider` / `embedding.model` / `embedding.base_url` / `embedding.api_key`：向量化模型配置，重建索引时会使用。
- `property_graph.enabled`：是否构建属性图谱。
- `property_graph.extraction_prompt`：图谱三元组抽取提示词。
- `metadata_extraction.enable_custom_scoring`：是否开启 LLM 自定义重要性打分；开启后会明显增加索引构建成本。

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
- `defaults`：Agent 默认启停、模式、超时和重试策略。
- `profiles.llm`：可复用的 LLM 连接配置。
- `profiles.embedding`：可复用的 embedding 配置。
- `bindings.agents.nlu`：NLU 绑定；当前为 `rule_first`。
- `bindings.agents.gm`：GM 叙事绑定；当前为 `llm_first`。
- `bindings.agents.evolution`：外环演化 Agent 绑定；当前默认关闭。
- `web_api.turn_timeout_seconds`：Web 回合超时配置记录；当前 Web 实现仍以 `web_api/service.py` 中的 `TURN_TIMEOUT_SECONDS = 180` 为准。

只验证确定性主链路时，可以关闭 GM 模型：

```yaml
bindings:
  agents.gm:
    enabled: false
    mode: "deterministic"
    llm_profile: null
```

### `config/main_loop_rules.json`

控制主循环的确定性规则和运行策略。

常改字段：
- `nlu.action_keywords`：动作关键词映射，例如移动、观察、攻击、使用物品。
- `nlu.target_aliases` / `location_aliases` / `item_aliases`：目标、地点、物品别名。
- `resolution`：确定性结算规则，例如攻击 DC、伤害骰、移动消耗、休息恢复。
- `rag.read_only_enabled`：主循环是否读取 RAG 上下文。
- `rag.auto_initialize`：是否允许运行时自动初始化 RAG。
- `outer_loop`：外环事件投递、补偿重放、超时、世界演化时间步长。
- `scene_defaults`：缺省场景、可用行动、建议行动。
- `narrative_templates`：模型不可用或确定性渲染时使用的叙事模板。

### `config/rag_import_rules.json`

记录知识库分组、标签和文件路径。通常建议用 `tools/doc_importer.py` 更新它，而不是手写。

分组字段含义：
- `group_name`：分组名。
- `description`：分组说明。
- `tags`：检索标签。
- `file_paths`：纳入该分组的文档路径。
- `enable_graph`：该分组是否参与图谱构建。

### `config/mod_registry.yml`

记录当前扫描到的 MOD 及其启用状态、优先级、冲突策略和钩子清单。通常由 `python tools/mod_manager.py scan` 生成或同步。

常改字段：
- `active_mods[].enabled`：临时启停某个 MOD。
- `active_mods[].priority`：调整 MOD 覆盖顺序。
- `active_mods[].conflict_strategy`：调整冲突处理策略。

## API 概览

- 创建会话：`POST /api/sessions`
- 普通回合：`POST /api/sessions/{session_id}/turns`
- SSE 流式回合：`POST /api/sessions/{session_id}/turns/stream`

更完整的契约回归清单见 `MANUAL_TEST_GUIDE.md`。

## 文档入口

- 玩家游玩指南：`PLAY_GUIDE.md`

## 目录结构

- `web_api/`：Flask 契约 API + `/play` 页面
- `game_workflows/`：主循环与外环工作流桥接
- `agents/`：智能体（NLU/GM/演化等）
- `state/`：数据契约与 SQLite 持久化工具
- `tools/`：确定性工具与 RAG 相关工具
- `mods/`：MOD 扩展与脚本
