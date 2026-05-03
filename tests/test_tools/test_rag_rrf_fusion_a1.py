from __future__ import annotations

from llama_index.core.schema import NodeWithScore, TextNode

from tools.rag.fusions.rrf_fusion import reciprocal_rank_fusion


def _node(node_id: str, text: str = "") -> NodeWithScore:
    """
    功能：构造带稳定 node_id 的 LlamaIndex 测试节点。
    入参：node_id（str）：节点唯一标识；text（str，默认空）：节点正文。
    出参：NodeWithScore，score 仅作为原始检索占位，RRF 会重算分数。
    异常：TextNode 构造失败时向上抛出，表示测试夹具非法。
    """
    return NodeWithScore(node=TextNode(id_=node_id, text=text or node_id), score=0.0)


class _BrokenNodeWithScore:
    """
    功能：模拟缺少 node_id 的异常检索结果，覆盖 RRF 输入防御分支。
    入参：无。
    出参：对象实例，仅提供 node 字段。
    异常：无显式异常；访问缺失 node_id 时由被测函数决定降级策略。
    """

    def __init__(self) -> None:
        self.node = object()


def test_rrf_returns_empty_for_empty_inputs() -> None:
    """
    功能：验证 RRF 对空输入和空路结果返回空列表。
    入参：无。
    出参：None。
    异常：断言失败表示空输入边界回归。
    """
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []


def test_rrf_merges_duplicate_nodes_and_sorts_by_fused_score() -> None:
    """
    功能：验证多路重复节点会累加 RRF 分数，并按融合分数降序返回。
    入参：无，使用内联节点。
    出参：None。
    异常：断言失败表示排序或去重契约回归。
    """
    node_a_first = _node("a", "A first")
    node_b = _node("b")
    node_c = _node("c")
    node_a_second = _node("a", "A second")

    fused = reciprocal_rank_fusion(
        [
            [node_a_first, node_b],
            [node_c, node_a_second],
        ],
        k=60,
        top_n=3,
    )

    assert [item.node.node_id for item in fused] == ["a", "c", "b"]
    assert fused[0].node.text == "A second"
    assert fused[0].score > fused[1].score > fused[2].score


def test_rrf_applies_top_n_truncation() -> None:
    """
    功能：验证 top_n 会截断融合结果数量，避免上游召回过多节点污染上下文。
    入参：无。
    出参：None。
    异常：断言失败表示 top_n 边界回归。
    """
    fused = reciprocal_rank_fusion([[_node("a"), _node("b"), _node("c")]], top_n=2)

    assert [item.node.node_id for item in fused] == ["a", "b"]


def test_rrf_skips_nodes_missing_node_id() -> None:
    """
    功能：验证异常检索结果缺少 node_id 时会被跳过，合法节点仍可融合返回。
    入参：无，使用异常节点替身。
    出参：None。
    异常：断言失败表示 RAG 融合缺少 malformed node 降级保护。
    """
    malformed = _BrokenNodeWithScore()

    fused = reciprocal_rank_fusion([[malformed, _node("valid")]], top_n=5)  # type: ignore[list-item]

    assert [item.node.node_id for item in fused] == ["valid"]
