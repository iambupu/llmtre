"""
功能：覆盖 agent context 的回归测试。
"""

from agents.agent_context import (
    initialize_session_agent_memory,
    load_agent_memory,
    merge_recent_memory,
    write_session_agent_memory,
)


def test_load_agent_memory_filters_placeholder_comments(tmp_path) -> None:
    """
    功能：验证 Agent 记忆读取会跳过 HTML 占位注释，只保留可注入的真实摘要。
    入参：tmp_path（pytest fixture）：临时上下文目录。
    出参：None。
    异常：断言失败表示 Agent 记忆读取或过滤规则回归。
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
    功能：验证短期会话记忆排在长期 Agent 记忆之前，避免长期摘要覆盖当前上下文。
    入参：无。
    出参：None。
    异常：断言失败表示记忆合并优先级回归。
    """
    merged = merge_recent_memory("第1回合：观察营地", "玩家曾与守卫结盟")

    assert merged.index("第1回合：观察营地") < merged.index("玩家曾与守卫结盟")
    assert "## 会话近期记忆" in merged
    assert "## Agent长期记忆" in merged


def test_load_agent_memory_uses_session_specific_file(tmp_path) -> None:
    """
    功能：验证有 session_id 时只读取该会话自己的长期记忆文件，不混入全局模板。
    入参：tmp_path（pytest fixture）：临时 Agent 上下文目录。
    出参：None。
    异常：断言失败表示不同游戏会话可能串记忆。
    """
    context_dir = tmp_path / ".agent_context"
    (context_dir / "sessions" / "sess_alpha01").mkdir(parents=True)
    (context_dir / "sessions" / "sess_beta01").mkdir(parents=True)
    (context_dir / "MEMORY.md").write_text("# 全局\n全局旧记忆不应注入。\n", encoding="utf-8")
    (context_dir / "sessions" / "sess_alpha01" / "MEMORY.md").write_text(
        "# 会话长期记忆\n玩家救下了甲会话的向导。\n",
        encoding="utf-8",
    )
    (context_dir / "sessions" / "sess_beta01" / "MEMORY.md").write_text(
        "# 会话长期记忆\n玩家激怒了乙会话的守卫。\n",
        encoding="utf-8",
    )

    memory = load_agent_memory(context_dir, session_id="sess_alpha01")

    assert "甲会话" in memory
    assert "乙会话" not in memory
    assert "全局旧记忆" not in memory


def test_write_session_agent_memory_creates_isolated_file(tmp_path) -> None:
    """
    功能：验证会话摘要会写入 `.agent_context/sessions/<session_id>/MEMORY.md`。
    入参：tmp_path（pytest fixture）：临时 Agent 上下文目录。
    出参：None。
    异常：断言失败表示会话长期记忆文件未创建或不可被读取。
    """
    context_dir = tmp_path / ".agent_context"

    memory_path = write_session_agent_memory(
        session_id="sess_alpha01",
        memory_summary="第1回合：玩家进入雾林。",
        context_dir=context_dir,
    )

    assert memory_path == context_dir / "sessions" / "sess_alpha01" / "MEMORY.md"
    assert memory_path is not None
    assert memory_path.exists()
    assert "玩家进入雾林" in load_agent_memory(context_dir, session_id="sess_alpha01")


def test_initialize_session_agent_memory_creates_empty_template(tmp_path) -> None:
    """
    功能：验证新会话会先创建空长期记忆模板，但模板不会进入 Agent prompt。
    入参：tmp_path（pytest fixture）：临时 Agent 上下文目录。
    出参：None。
    异常：断言失败表示空模板可能污染叙事上下文。
    """
    context_dir = tmp_path / ".agent_context"

    memory_path = initialize_session_agent_memory("sess_alpha01", context_dir=context_dir)

    assert memory_path == context_dir / "sessions" / "sess_alpha01" / "MEMORY.md"
    assert memory_path is not None
    assert memory_path.exists()
    assert load_agent_memory(context_dir, session_id="sess_alpha01") == ""
