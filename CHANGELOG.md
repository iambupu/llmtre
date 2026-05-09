# Changelog

本文件记录 TRE / llmtre 的用户可见变化、发布门禁和重要兼容说明。

## [0.1.0-a2] - 2026-05-09

### Added

- 新增 Story Pack v0 系统：`state/contracts/story_pack.py`（SceneContract、LoreEntry、InteractionRule、CharacterContract v2、StoryPackManifest）。
- 新增 `tools/packs/registry.py`：StoryPackRegistry 与 validate_story_pack 校验器。
- 新增 `tools/packs/validate.py` CLI 入口，支持 `python -m tools.packs.validate <pack>` 命令行校验。
- 新增 `web_api/blueprints/story_packs.py` Blueprint，提供 `GET /api/story-packs` 列表与 `GET /api/story-packs/<pack_id>` 详细信息（manifest/scenes）端点。
- 新增 `web_api/blueprints/sessions.py` 中 `pack_id`/`scenario_id` 的会话创建绑定与 idempotency 预检查。
- 新增 `state/tools/runtime_schema.py` ALTER TABLE IF NOT EXISTS，为 sessions 表追加 5 个 A2 剧本运行时状态列。
- 新增 `game_workflows/main_loop_config.py` 中 `default_story_policy` 配置字段。
- 新增 `story_packs/demo_a2_core/` 演示剧本包（3 个场景、4 个交互入口、1 条 lore）。
- 新增 `config/api/openapi.yaml` StoryPacks API 端点规范。
- 新增 `tests/test_web_api/test_sessions_a2.py` A2 会话绑定回归测试。

### Changed

- `get_idempotent_response` 签名扩展：新增 `scope`、`session_id`、`request_id` 参数。
- `play_state` 的 `init_state` 现在包含 `pack_metadata`。
- NLU 策略调整：从 `DEFAULT_MAIN_LOOP_RULES` 中清除 3 条硬编码 location_aliases（`r_entrance`、`ruins_entrance`、`遗迹入口`），改由 StoryPack 驱动的场景上下文提供。
- sessions 表通过运行期 schema 追加剧本运行时状态列。

### Verification

- pytest: 341 passed, 0 failed（`python -m pytest tests -q`）
- ruff: All checks passed（`python -m ruff check .`）
- mypy: Success（`python -m mypy .` — 98 source files, 0 errors）
- 发布前代码审查：P0 无 / P1 4/4 闭环 / P2 3/3 闭环。合入判断：可发布。

### Known Notes

- Story Pack v0 为本地 JSON 文件夹格式，manifest/scenes/lore 文件需通过 CLI 校验通过后方可注册到运行时。
- 非法或未通过校验的 pack 不会出现在 API 响应中，也不会污染数据库会话表。
- demo_a2_core 包与 A1 默认场景共存；创建会话时未指定 pack_id 的行为与 A1 一致。

## [0.1.0-a1] - 2026-05-07

### Added

- 新增 `/app` React 前端入口，保留 `/play` legacy playground 作为对照和回归入口。
- 新增 A1 回合契约字段：`session_turn_id`、`runtime_turn_id`、`trace_id`、`outcome`、`trace`。
- 新增 `TurnTrace` 阶段追踪，覆盖 API、主循环、GM 渲染、外环投递与持久化阶段。
- 新增 SceneSnapshot v2 结构化场景能力，包括 `scene_objects`、`interaction_slots`、`affordances` 和 UI hints。
- 新增最小 `AgentEnvelope` 与 `ClarifierAgent`，用于 Agent 内部结构化协议和模糊输入澄清。
- 新增事件总线事务级运行日志，日志验收现在要求事件触发、写计划开始与事务提交证据。
- 新增 wheel 发布资源配置，发布包包含 `config/`、`templates/` 和 `static/` 运行时资源。

### Changed

- quick actions 收口为后端权威 affordance 派生结果，GM 与前端不得展示未授权动作。
- 普通回合与 SSE `done` 负载保持同构，共享响应构建和契约校验。
- SSE 回合固定阶段反馈：`received`、`loading_scene`、`parsing_nlu`、`validating_action`、`resolving_action`、`rendering_gm`、`done/error`。
- `use_item` 改为检查并消费背包物品，`attack` 使用确定性随机种子的命中和伤害检定。
- 调试面板不再展示伪造成功率或耗时，改为基于真实 trace/SSE/错误状态展示。
- `reset_session` 普通路径不再依赖 `main_loop` 属性，仅沙盒 reset 需要主循环数据库工具。
- `load_main_loop_rules.cache_clear()` 兼容入口恢复，便于测试和配置热更新场景清理缓存。

### Fixed

- 修复 LLM/隐藏块 quick actions 绕过 affordance 约束的问题。
- 修复 Web API quick action layout 将未匹配动作放入公共布局的问题。
- 修复 `trace: null` 导致普通/SSE 响应调试契约漂移的问题。
- 修复 wheel 产物缺少模板、静态资源和运行配置的问题。
- 修复 MOD 脚本包边界导致 mypy 重复模块识别的问题。
- 修复安全脚本求值器对字典展开 AST 的类型边界。

### Verification

- `python -m pytest tests -q --basetemp=test_runs\pytest_tmp_prerelease_review`：324 passed。
- `python -m ruff check .`：通过。
- `python -m mypy .`：通过。
- `npm run typecheck`：通过。
- `npm run build`：通过。
- `python -m tools.logs.check_runtime_logs --since-minutes 120`：通过。

### Known Notes

- `python -m black --check .` 当前仍会重排一批历史文件；A1 暂不在本次功能修复中执行全仓格式化。
- `.code_md/` 与 `.coding_docs/` 当前按仓库策略被 `.gitignore` 忽略；本地发布记录已更新，但默认不会进入 git 跟踪。
- 前端调试输出 `frontend/stream_debug*.txt` 已加入 `.gitignore`，不作为发布内容。
