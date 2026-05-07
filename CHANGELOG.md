# Changelog

本文件记录 TRE / llmtre 的用户可见变化、发布门禁和重要兼容说明。

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
