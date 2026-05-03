from __future__ import annotations

from typing import Any

from llama_index.core.schema import QueryBundle

from tools.rag.unified_retriever import HybridRetriever, UnifiedRetriever


class _FakeNode:
    """
    功能：为 RAG 检索测试提供最小 node 对象，支持 get_content。
    入参：text（str）：节点文本。
    出参：_FakeNode。
    异常：不抛异常。
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self.node_id = text

    def get_content(self) -> str:
        """
        功能：返回节点内容。
        入参：无。
        出参：str。
        异常：不抛异常。
        """
        return self._text


class _FakeNodeWithScore:
    """
    功能：模拟 NodeWithScore 的最小接口（仅保留 node 字段）。
    入参：text（str）：节点文本。
    出参：_FakeNodeWithScore。
    异常：不抛异常。
    """

    def __init__(self, text: str) -> None:
        self.node = _FakeNode(text)
        self.score = 1.0


class _FakeRetriever:
    """
    功能：模拟 retriever，返回预设节点列表。
    入参：nodes（list[Any]）：检索返回结果；raise_on_retrieve（bool）：是否抛异常。
    出参：_FakeRetriever。
    异常：按配置抛 RuntimeError 以覆盖异常分支。
    """

    def __init__(self, nodes: list[Any], raise_on_retrieve: bool = False) -> None:
        self._nodes = nodes
        self._raise_on_retrieve = raise_on_retrieve

    def retrieve(self, _query_bundle: Any) -> list[Any]:
        """
        功能：返回预设检索结果。
        入参：_query_bundle（Any）：查询参数（测试中不关心）。
        出参：list[Any]。
        异常：raise_on_retrieve=True 时抛 RuntimeError。
        """
        if self._raise_on_retrieve:
            raise RuntimeError("graph failed")
        return self._nodes


def test_unified_retriever_readonly_returns_message_when_vector_missing(monkeypatch) -> None:
    """
    功能：验证向量索引缺失时 query_readonly 返回稳定降级文案。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示无索引分支回归。
    """
    monkeypatch.setattr(UnifiedRetriever, "_load_indices", lambda self: None)
    retriever = UnifiedRetriever(config={})
    retriever._vector_index = None
    assert retriever.query_readonly("test") == "检索失败：向量库尚未初始化。"


def test_unified_retriever_cache_key_and_cache_refresh(monkeypatch) -> None:
    """
    功能：验证混合检索器缓存键变化会触发重建，键不变时复用缓存实例。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示缓存策略分支退化。
    """
    monkeypatch.setattr(UnifiedRetriever, "_load_indices", lambda self: None)
    retriever = UnifiedRetriever(config={})
    calls = {"build": 0}

    def _fake_build() -> str:
        calls["build"] += 1
        return f"hybrid_{calls['build']}"

    monkeypatch.setattr(retriever, "_build_hybrid_retriever", _fake_build)
    monkeypatch.setattr(retriever, "_build_hybrid_cache_key", lambda: (1, False))
    first = retriever._get_hybrid_retriever()
    second = retriever._get_hybrid_retriever()
    assert first == "hybrid_1"
    assert second == "hybrid_1"
    assert calls["build"] == 1

    monkeypatch.setattr(retriever, "_build_hybrid_cache_key", lambda: (2, False))
    third = retriever._get_hybrid_retriever()
    assert third == "hybrid_2"
    assert calls["build"] == 2


def test_unified_retriever_query_readonly_joins_fused_nodes(monkeypatch) -> None:
    """
    功能：验证 query_readonly 会拼接融合节点内容，并处理空节点分支。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示只读拼接分支退化。
    """
    monkeypatch.setattr(UnifiedRetriever, "_load_indices", lambda self: None)
    retriever = UnifiedRetriever(config={})
    retriever._vector_index = object()
    monkeypatch.setattr(
        retriever,
        "_get_hybrid_retriever",
        lambda: _FakeRetriever([_FakeNodeWithScore("a"), _FakeNodeWithScore("b")]),
    )
    assert retriever.query_readonly("q") == "a\n\n---\n\nb"
    monkeypatch.setattr(retriever, "_get_hybrid_retriever", lambda: _FakeRetriever([]))
    assert retriever.query_readonly("q") == ""


def test_hybrid_retriever_handles_graph_failure_and_still_fuses(monkeypatch, caplog) -> None:
    """
    功能：验证图谱检索抛错时 HybridRetriever 记录告警并继续执行 RRF 融合。
    入参：monkeypatch/caplog（pytest fixtures）：替换工具与日志捕获器。
    出参：None。
    异常：断言失败表示图谱异常降级分支退化。
    """
    vector = _FakeRetriever([_FakeNodeWithScore("v1")])
    bm25 = _FakeRetriever([_FakeNodeWithScore("b1")])
    graph = _FakeRetriever([], raise_on_retrieve=True)
    merged = [_FakeNodeWithScore("fused")]
    monkeypatch.setattr(
        "tools.rag.unified_retriever.reciprocal_rank_fusion",
        lambda results, top_n=5: merged,
    )
    hybrid = HybridRetriever(vector, bm25, graph)
    with caplog.at_level("WARNING"):
        fused = hybrid._retrieve(QueryBundle("query"))
    assert fused == merged
    assert "图谱检索失败" in caplog.text
