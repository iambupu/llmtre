from agents.agent_context import load_agent_memory, merge_recent_memory


def test_load_agent_memory_filters_placeholder_comments(tmp_path) -> None:
    """
    功能：验证 Agent 记忆读取会跳过 HTML 占位注释，只保留可注入的真实摘要。
    入参：tmp_path（pytest fixture）：临时上下文目录。
    出参：None。
    异常：断言失败表示 `.agent_context/MEMORY.md` 读取或过滤规则回归。
    """
    context_dir = tmp_path / ".agent_context"
    context_dir.mkdir()
    (context_dir / "MEMORY.md").write_text(
        "# 长期记忆\n<!-- 占位提示不应进入 prompt -->\n玩家救下了矿洞里的向导。\n",
        encoding="utf-8",
    )

    memory = load_agent_memory(context_dir)

    assert "玩家救下了矿洞里的向导" in memory
    assert "占位提示" not in memory


def test_load_agent_memory_missing_file_returns_empty(tmp_path) -> None:
    """
    功能：验证 Agent 记忆文件缺失时按空记忆降级，不阻断主循环。
    入参：tmp_path（pytest fixture）：未写入 MEMORY.md 的临时目录。
    出参：None。
    异常：断言失败表示缺失文件降级契约回归。
    """
    assert load_agent_memory(tmp_path / ".agent_context") == ""


def test_load_agent_memory_heading_only_template_returns_empty(tmp_path) -> None:
    """
    功能：验证只有章节标题和占位注释的 MEMORY.md 不会被当成有效剧情记忆。
    入参：tmp_path（pytest fixture）：临时上下文目录。
    出参：None。
    异常：断言失败表示空模板会污染 Agent prompt。
    """
    context_dir = tmp_path / ".agent_context"
    context_dir.mkdir()
    (context_dir / "MEMORY.md").write_text(
        "# 长期记忆\n\n## 会话历史\n<!-- 此处记录游戏会话的递归摘要 -->\n",
        encoding="utf-8",
    )

    assert load_agent_memory(context_dir) == ""


def test_merge_recent_memory_keeps_session_memory_first() -> None:
    """
    功能：验证短期会话记忆排在长期 Agent 记忆之前，避免跨会话摘要覆盖当前上下文。
    入参：无。
    出参：None。
    异常：断言失败表示记忆合并优先级回归。
    """
    merged = merge_recent_memory("第1回合：观察营地", "玩家曾与守卫结盟")

    assert merged.index("第1回合：观察营地") < merged.index("玩家曾与守卫结盟")
    assert "## 会话近期记忆" in merged
    assert "## Agent长期记忆" in merged
