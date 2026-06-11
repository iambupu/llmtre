# .agent_context 目录说明

`.agent_context/` 是 TRE 运行期 Agent 上下文的本地工作区，用于保存智能体可读取的长期叙事摘要与操作规范。它服务于 Agent 的 prompt 上下文挂载，不是确定性状态库，也不是 Web 会话历史表的替代品。

## 当前文件

- `AGENTS.md`：定义运行期 Agent 如何分层读取、写入与压缩上下文。
- `OPS.md`：定义 Agent 工具调用、数据流和错误记录的硬性操作规范。
- `MEMORY.md`：全局模板/兼容入口；运行期有 Web 会话时不再作为共享长期记忆源。
- `sessions/<session_id>/MEMORY.md`：每个游戏会话自己的长期叙事记忆镜像，由结构化记忆项导出生成。

## 运行期接入

主循环通过 `agents.agent_context.load_agent_memory()` 只读加载 `.agent_context/sessions/<session_id>/MEMORY.md`，再由 `MainEventLoop.run()` 将其与 Web 会话近期摘要合并，挂载到 `SceneSnapshot.recent_memory`。GM、NLU 和后续叙事渲染链路通过现有 `scene_snapshot` 读取该上下文。

挂载顺序固定为：

1. Web 会话近期记忆：来自 session turn 历史，反映当前玩家最近几轮行为。
2. Agent 长期记忆：来自 `.agent_context/sessions/<session_id>/MEMORY.md`，只补充该会话的长期背景。

## 边界

- 会话隔离：每个 `session_id` 对应独立 `sessions/<session_id>/MEMORY.md`，避免多个游戏会话互相串记忆。
- DB 权威：结构化长期叙事记忆项存储在 SQLite，`MEMORY.md` 只是人类可读镜像；同步失败只记录告警，不改变数据库权威性。
- 不参与裁决：数值、背包、HP、位置、任务完成状态必须来自 SQLite/Pydantic 契约，不能依赖 `MEMORY.md`。
- 空模板忽略：只有标题或 HTML 占位注释的 `MEMORY.md` 会被视为空，不会进入 prompt。
- 本地语义：该目录用于开发和运行期上下文，不承诺作为远程发布资产；是否纳入版本控制由仓库策略单独决定。

## 写入建议

写入 `sessions/<session_id>/MEMORY.md` 时只记录高价值叙事事实，例如：

- 玩家做出的不可逆选择。
- NPC 阵营、城镇态度、任务分支的长期变化。
- 沙盒回滚、异常恢复等会影响后续叙事解释的事件。

不要记录可由数据库确定查询得到的事实，例如当前 HP、金币数量、背包中是否有某个物品。
