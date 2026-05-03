from __future__ import annotations

import pytest

from game_workflows.rag_readonly_bridge import RAGReadOnlyBridge


def test_build_snapshot_returns_disabled_payload_when_bridge_disabled() -> None:
    """
    功能：验证桥接层禁用时返回稳定降级结构，不触发任何 RAG 初始化。
    入参：无。
    出参：None。
    异常：断言失败表示禁用分支契约回归。
    """
    bridge = RAGReadOnlyBridge(enabled=False)
    payload = bridge.build_snapshot("世界观")
    assert payload["rag_enabled"] is False
    assert payload["rag_ready"] is False
    assert payload["rag_context"] == ""
    assert "禁用" in payload["rag_error"]


def test_build_snapshot_auto_init_calls_update_index_when_docstore_missing(monkeypatch) -> None:
    """
    功能：验证 auto_initialize 开启且向量 docstore 缺失时会触发 update_index。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示初始化分支退化。
    """
    calls = {"ensure_sqlite": 0, "update_index": 0}

    class _FakeManager:
        def update_index(self) -> None:
            calls["update_index"] += 1

        def query_lore_readonly(self, query: str) -> str:
            return f"ctx:{query}"

    monkeypatch.setattr(
        "game_workflows.rag_readonly_bridge.RAGManager",
        _FakeManager,
    )
    monkeypatch.setattr(
        "game_workflows.rag_readonly_bridge.os.path.exists",
        lambda path: False,
    )

    bridge = RAGReadOnlyBridge(enabled=True, auto_initialize=True)
    monkeypatch.setattr(bridge, "_ensure_sqlite", lambda: calls.__setitem__("ensure_sqlite", 1))
    payload = bridge.build_snapshot("古代王国")
    assert calls["ensure_sqlite"] == 1
    assert calls["update_index"] == 1
    assert payload["rag_ready"] is True
    assert payload["rag_context"] == "ctx:古代王国"


def test_build_snapshot_returns_error_payload_when_query_fails(monkeypatch) -> None:
    """
    功能：验证查询异常时返回 rag_ready=False 与错误文本，确保主循环可降级运行。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示异常降级分支回归。
    """

    class _BrokenManager:
        def query_lore_readonly(self, _query: str) -> str:
            raise RuntimeError("vector unavailable")

    bridge = RAGReadOnlyBridge(enabled=True, auto_initialize=False)
    monkeypatch.setattr(bridge, "_ensure_manager", lambda: _BrokenManager())
    payload = bridge.build_snapshot("线索")
    assert payload["rag_enabled"] is True
    assert payload["rag_ready"] is False
    assert payload["rag_context"] == ""
    assert payload["rag_error"] == "vector unavailable"


@pytest.mark.asyncio
async def test_build_snapshot_async_wraps_sync_path(monkeypatch) -> None:
    """
    功能：验证异步接口会复用同步构建逻辑并返回一致结构。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示异步包装分支回归。
    """
    bridge = RAGReadOnlyBridge(enabled=True, auto_initialize=False)
    monkeypatch.setattr(
        bridge,
        "build_snapshot",
        lambda query: {
            "rag_enabled": True,
            "rag_ready": True,
            "rag_query": query,
            "rag_context": "ok",
            "rag_error": "",
        },
    )
    payload = await bridge.build_snapshot_async("异步查询")
    assert payload["rag_query"] == "异步查询"
    assert payload["rag_context"] == "ok"
