# TRE A1版本游玩指南

本指南面向想直接试玩当前 Web Demo 的玩家。按本文顺序操作，可以完成一次新会话创建、回合输入、场景查看、记忆查看和重启后继续游玩。当前推荐使用 `/app`（React 前端），`/play` 作为 legacy 对照入口保留。

## 1. 游玩前准备

### 1.1 安装依赖

项目要求 Python `3.14+`。

如果你希望少处理虚拟环境，推荐先安装 `uv`，再在项目根目录运行：

```bash
uv sync
```

`uv` 会自动创建/使用本地环境，并安装项目依赖。官方安装说明见 [uv 安装页](https://docs.astral.sh/uv/getting-started/installation/)。

如果你更习惯传统方式，也可以继续用：

```bash
pip install -r requirements.txt
```

### 1.2 准备本地模型

当前验证过的默认配置使用 Ollama：

- LLM：`qwen3:8b`
- Embedding：`bge-m3`

如果使用其他模型，需要同步修改：

- RAG 模型：`config/rag_config.yml`
- Agent 叙事模型：`config/agent_model_config.yml`

### 1.3 初始化数据库、导入文档和加载 MOD

首次运行推荐按顺序执行这三条命令，它们分别负责三件不同的事：

- `uv run python state/tools/db_initializer.py`：初始化或重建 SQLite 游戏状态库，写入种子数据，让会话、角色、背包、任务等基础状态可用。
- `uv run python tools/doc_importer.py docs/ --group core --sync`：把 `docs/` 里的规则书、设定文档导入 RAG，并立即重建索引，让系统能读到最新文档。
- `uv run python tools/mod_manager.py scan`：扫描 `mods/` 下的 MOD，更新 `config/mod_registry.yml`，让新增或修改过的 MOD 进入注册表。

```bash
uv run python state/tools/db_initializer.py
uv run python tools/doc_importer.py docs/ --group core --sync
uv run python tools/mod_manager.py scan
```

说明：

- `docs/` 是知识库输入目录。先把规则书、世界观、设定文档放进 `docs/`，再导入 RAG。
- `db_initializer.py` 只负责把数据库和种子状态准备好，不会替你导入文档或启用 MOD。
- `doc_importer.py` 只负责导入文档和重建索引，不会替你创建数据库或扫描 MOD。
- `mod_manager.py scan` 只负责扫描注册 MOD，不会自动把它们全部设为启用。
- `uv run python app.py` 启动时也会尝试自动补齐 SQLite 和向量索引；如果自动索引失败，请回到本节手动执行初始化命令。
- 主循环规则不是固定只读 `config/main_loop_rules.json`：运行时会按“基础规则 + 已启用模组覆盖 + 剧本覆盖 + 额外覆盖”合并。普通玩家无需手动改规则文件。

#### 1.3.1 把 `docs/` 导入 RAG

`tools/doc_importer.py` 不只是“把 `docs/` 扔进索引”这么简单，它有三种常见用法：

1. 无参数运行：直接按现有 `config/rag_import_rules.json` 做一次同步。
2. 指定路径导入：把单个文件、普通目录或 MinerU 导出目录登记到某个分组。
3. 带 `--sync`：导入后立即重建 `knowledge_base/indices/`。

当你新增或更新规则书、设定文档时，通常运行：

```bash
uv run python tools/doc_importer.py docs/ --group core --sync
```

这条命令会做两件事：

1. 把 `docs/` 里的文档登记进 `config/rag_import_rules.json`
2. 重新构建 `knowledge_base/indices/`，让 RAG 能读到最新文档

常见用法：

- 导入整个 `docs/` 目录时，继续用 `--group core` 即可。
- 如果你只想导入单个文件，也可以把路径换成具体文件，例如 `docs/rules.md`。
- `--sync` 表示导入后立即重建索引；如果不加，就只更新规则，不马上重建索引。

如果你把文件整理成 MinerU 导出目录，可以加 `--mineru` 强制按目录方式处理。

更完整的参数说明：

- `path`：要导入的文件或目录。
- `--group <name>`：目标分组名称。只要你显式提供 `path`，就必须同时提供 `--group`。
- `--tags tag1,tag2`：给分组附加标签。
- `--desc "说明"`：写入分组描述。
- `--sync`：导入完成后立即同步索引。
- `--mineru`：把目录按 MinerU 导出结果处理，即使目录名本身不含标准标记也会按整体导入。

支持的路径类型：

- 单个文档文件，例如 `docs/rules.md`
- 普通目录，例如 `docs/`
- MinerU 导出目录，例如包含 `.md` 和 `.json` 的整理目录

示例：

```bash
uv run python tools/doc_importer.py docs/rules.md --group core --tags rules,story --desc "核心规则书" --sync
uv run python tools/doc_importer.py docs/setting/ --group setting --sync
uv run python tools/doc_importer.py docs/mineru_export/ --group lore --mineru --sync
```

如果你不传 `path` 和 `--group`，脚本会直接读取现有导入规则并同步索引，不会新增分组。

#### 1.3.2 扫描并加载 MOD

`tools/mod_manager.py scan` 用于扫描 `mods/` 下的 MOD 并写回 `config/mod_registry.yml`。它会读取每个含 `mod_info.json` 的 MOD 目录，把新发现的 MOD 登记进注册表。

当你在 `mods/` 下新增或修改 MOD 后，先运行：

```bash
uv run python tools/mod_manager.py scan
```

这条命令会扫描 `mods/` 中包含 `mod_info.json` 的目录，并更新 `config/mod_registry.yml`。

扫描后请检查 `config/mod_registry.yml` 中对应 MOD 的 `enabled`：

- `enabled: true` 表示加载这个 MOD
- `enabled: false` 表示只登记，不启用

如果你刚添加了一个 MOD，通常需要把它的 `enabled` 改成 `true`，然后重新启动 `uv run python app.py`，这样 MOD 才会真正进入运行时。

你通常还会顺手检查：

- `priority`：优先级，数值越大越优先。
- `conflict_strategy`：冲突处理策略。
- `hooks_manifest`：这个 MOD 声明了哪些钩子和写入字段。

## 2. 启动游戏

```bash
uv run python app.py
```

启动后在浏览器打开（推荐）：

```text
http://localhost:5000/app
```

如需对照 legacy 页面，再打开：

```text
http://localhost:5000/play
```

如果访问 `/app` 看到引导页提示 `frontend/dist/index.html` 缺失，说明还未构建前端。请在 `frontend/` 目录执行：

```bash
npm install
npm run build
```

## 3. 页面区域说明

`/app` 页面主要分为四块：

- 顶部栏：填写角色 ID、会话 ID，执行 `新会话` / `加载` / `重置`，以及 `控制台/调试` 开关。`新会话` / `加载` 只在这里出现。
- 场景区：展示当前地点标题、出口徽标、可见对象卡片、当前状态提示（不再是原始 JSON 直出）。
- 回合记录：展示系统/玩家/GM 消息、快捷行动按钮、输出模式（`stream` / `sync`）、输入框与 `发送` / `停止`。
- 右侧状态区：角色状态、背包/装备、任务、记忆摘要与沙盒控制按钮；角色信息由后端会话返回驱动，未建会话前显示 `--` 占位。

玩家日常游玩主要使用：

- `新会话`：创建一次新冒险。
- `发送`：提交当前输入。
- `/app` 记忆区按钮：`读取` / `刷新` / `清空`。
- `/app` 沙盒区按钮：`并入` / `回滚`。

如果你在看 legacy `/play` 页面，对应按钮文案是：

- `记忆` / `刷新记忆`
- `并入主线` / `回滚沙盒`

## 4. 第一局推荐流程

1. 确认顶部“角色”为 `player_01`。
2. 点击 `新会话`。
3. 等待 GM 开场叙事显示在“回合记录”中。
4. 阅读“当前场景”，重点看地点描述、出口、可见对象和建议行动。
5. 点击页面给出的建议行动，或在输入框输入 `观察周围` 后点击 `发送`。
6. 继续输入 3 到 5 个明确行动，例如：

```text
观察周围
检查背包
前往森林
攻击地精
和地精说话
使用药水
```

7. 点击记忆区的 `读取` 查看近期记忆文本（由有效回合拼接/分段摘要生成）。
8. 点击记忆区的 `刷新` 触发后端重算摘要，确认记忆文本更新。
9. 关闭并重新启动 `uv run python app.py` 后，可把顶部“会话”输入框填回原 `session_id`，点击 `加载` 继续游玩。

## 5. 如何输入行动

当前 NLU 以规则和关键词为主，输入越明确越稳定。

| 想做的事 | 推荐输入 |
| :--- | :--- |
| 查看环境 | `观察周围`、`环顾四周` |
| 检查状态 | `检查背包`、`查看当前场景` |
| 移动 | `前往森林`、`移动到营地`、`继续前进` |
| 对话 | `和地精说话`、`与旅行者对话` |
| 互动 | `调查营地`、`尝试互动` |
| 使用物品 | `使用药水`、`喝下药水` |
| 休息等待 | `休息`、`等待一会` |
| 攻击 | `攻击地精` |

建议避免过短或缺目标的输入，例如 `过去`、`看看`、`弄一下`。如果系统认为行动不够明确，会返回澄清问题；按问题补充目标或方向即可。

## 6. 场景、记忆与建议行动

“当前场景”来自后端返回的 `SceneSnapshot`，会随创建会话、加载会话和提交回合刷新。

你需要重点关注：

- 当前位置：当前地点名称与描述。
- 出口：可以移动到哪里。
- 可见对象：当前可见 NPC、物品或任务。
- 建议行动：页面可点击的下一步行动。
- 最近记忆：系统记录的近期记忆文本（规则化拼接 + 可配置步长摘要）。

建议行动只是快捷输入，不是唯一可选项；你也可以直接在输入框输入其他明确行动。

## 7. 输出方式（/app）

默认建议使用 `stream`。提交回合后，前端会接收 SSE 事件，GM 文本逐段出现；调试面板可查看 `lastSseEvent` 和状态日志。

如果流式输出异常，可把输出模式切到 `sync` 后重试同一条输入，并对比调试面板里的 `lastRequest`、`trace` 与状态日志。

调试面板布局说明（与 `/app` 新版一致）：

- 上方：`状态 / Trace / 日志 / 内存` Tabs。
- 下方：对应功能区内容。
- `Trace` 标签页包含统计卡片、事件检索/筛选、时间线列表；当前无事件时显示空态提示。

## 8. 沙盒并入与回滚

当前页面直接暴露两个沙盒按钮，用于展示和操作沙盒分支。当前沙盒仍属于显示/实验功能，不要把它理解成正式剧情分支系统。

- `并入主线`：把当前 Shadow 分支变化合并到 Active 主线状态。
- `回滚沙盒`：放弃当前 Shadow 分支变化，恢复到 Active 主线状态。

沙盒功能允许你在不影响主线的情况下查看或管理沙盒变化。当前不会因为输入某些行动而自动切换沙盒模式，只有显式操作沙盒按钮时才会触发相关接口。

如果不确定当前会话是否处于沙盒分支，保持正常输入行动即可。沙盒控制的正式契约入口是专用接口 `POST /api/sessions/{session_id}/sandbox/commit` 与 `POST /api/sessions/{session_id}/sandbox/discard`；不要把普通 `/turns` 输入中的同名文本动作当作通用保存/撤销能力。

## 9. 重置与继续会话

- `重置`：清空当前会话的回合与记忆，默认保留角色信息，并把 `current_turn_id` 重置为 `0`。
- 如果当前会话处于沙盒模式，重置前还要满足沙盒 owner / 租约条件，否则会返回受控错误。
- `加载`：输入已有 `session_id` 后恢复该会话。

会话数据保存在 SQLite 中。只要没有删除或重建 `state/core_data/tre_state.db`，重启 Flask 后仍可加载旧会话。

## 10. 角色状态怎么看

- 右侧“角色状态”卡的 HP/MP、状态摘要和状态标签都来自后端 `active_character`。
- 状态摘要由 SQLite `state_flags_json`、HP/MP 阈值和分层规则 `character_status` 派生；前端只展示，不写入状态。
- 没有状态效果时显示“状态稳定”；未创建或加载会话前显示 `--`。
- 调试面板的“状态”页可查看原始 `state_flags/status_effects/status_context`，用于核对后端返回。

## 11. 常见问题

### 页面打不开

确认服务正在运行：

```bash
uv run python app.py
```

然后访问：

```text
http://localhost:5000/app
```

如 `/app` 显示前端构建缺失提示，请在 `frontend/` 执行 `npm run build` 后刷新。

### 创建会话或回合很慢

本地模型首次加载可能较慢。先确认 Ollama 正在运行，且 `qwen3:8b` 与 `bge-m3` 可用。

如果只想验证纯确定性链路，需要同时关闭 NLU 和 GM 的模型调用。仅关闭 GM 只会切换叙事模板，NLU 仍可能走 LLM 兜底。

`config/agent_model_config.yml` 示例：

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

RAG 索引缺失时，启动会尝试初始化向量索引，embedding 仍可能依赖 Ollama。纯确定性验收时建议先准备好索引，或在你的规则覆盖层（基础文件/模组覆盖/剧本覆盖）中临时关闭：

```json
{
  "rag": {
    "read_only_enabled": false,
    "auto_initialize": false
  }
}
```

### RAG 或知识库报错

重新导入并同步知识库：

```bash
uv run python tools/doc_importer.py docs/ --group core --sync
```

同时确认 `config/rag_config.yml` 中的 embedding 模型可用。

### 系统要求澄清

这不是失败。说明输入缺少目标、方向或动作类型。按澄清问题补充更具体的行动即可，例如把 `过去` 改成 `前往森林`。

### 想确认运行日志是否正常

试玩过至少一个回合后执行：

```bash
python -m tools.logs.check_runtime_logs --since-minutes 15
```

输出中包含 `RUNTIME_LOG_CHECK_OK` 表示近期主循环、事件总线和外环日志证据完整。
