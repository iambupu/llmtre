# TRE 最小可玩版本人工测试指南

本指南用于发布前人工确认 Web Demo 的核心链路是否可用。玩家操作说明见 `PLAY_GUIDE.md`。

## 1. 测试前准备

```bash
pip install -r requirements.txt
python state/tools/db_initializer.py
python tools/doc_importer.py docs/ --group core --sync
python tools/mod_manager.py scan
python app.py
```

启动后访问：

```text
http://localhost:5000/play
```

## 2. 自动检查

```bash
python -m ruff check .
python -m mypy .
python -m pytest tests -q
```

## 3. Web 回归清单

1. 点击 `新会话`，确认页面显示 GM 开场叙事、当前场景和建议行动。
2. 输入 `观察周围` 并点击 `发送`，确认回合记录出现玩家输入和 GM 响应。
3. 输入 3 到 5 个明确行动，确认回合可以连续推进。
4. 点击 `历史`，确认回合历史可以读取。
5. 点击 `记忆` 和 `刷新记忆`，确认记忆摘要可以读取和更新。
6. 输入模糊行动，例如 `过去`，确认系统返回澄清问题而不是崩溃。
7. 在调试面板 `配置` 中切换 `流式输出` / `普通输出`，确认两种提交方式都可用。
8. 点击 `并入主线` 和 `回滚沙盒`，确认按钮返回成功响应。
9. 记录当前 `session_id`，重启 `python app.py` 后输入该 ID 并点击 `加载`，确认会话可恢复。
10. 点击 `重置`，确认会话回合被清空，`current_turn_id` 回到 `0`。

## 4. 日志验收

完成至少一个普通回合后执行：

```bash
python -m tools.logs.check_runtime_logs --since-minutes 15
```

期望输出包含：

```text
RUNTIME_LOG_CHECK_OK
```

如需验证历史发布基线，可额外执行：

```bash
python -m tools.logs.stage_d_acceptance_check
```

期望输出包含：

```text
STAGE_D_ACCEPTANCE_OK
```
