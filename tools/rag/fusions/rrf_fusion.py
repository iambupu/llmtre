from llama_index.core.schema import BaseNode, NodeWithScore


def reciprocal_rank_fusion(
    results: list[list[NodeWithScore]],
    k: int = 60,
    top_n: int = 5,
) -> list[NodeWithScore]:
    """
    功能：Reciprocal Rank Fusion (RRF) 算法实现。
    入参：results（list[list[NodeWithScore]]）：多路检索结果，按路内顺序表示排名；
        k（int，默认 60）：RRF 平滑常量，需为正整数；top_n（int，默认 5）：返回上限。
    出参：list[NodeWithScore]，按融合分数降序排列且按 node_id 去重。
    异常：缺少 node/node_id 的异常检索项会被跳过；NodeWithScore 构造异常向上抛出。
    """
    if top_n <= 0:
        return []
    safe_k = max(1, k)
    fused_scores: dict[str, float] = {}
    nodes_map: dict[str, BaseNode] = {}

    for retriever_results in results:
        for rank, node_with_score in enumerate(retriever_results):
            node = getattr(node_with_score, "node", None)
            node_id = getattr(node, "node_id", None)
            if not isinstance(node_id, str) or not node_id:
                # 降级路径：上游检索器偶发返回坏节点时跳过，保留其他合法召回结果。
                continue
            nodes_map[node_id] = node

            # RRF Score = 1 / (rank + k)
            if node_id not in fused_scores:
                fused_scores[node_id] = 0.0
            fused_scores[node_id] += 1.0 / (rank + safe_k)

    # 排序
    sorted_node_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)

    # 转换为 NodeWithScore 列表
    final_results: list[NodeWithScore] = []
    for node_id in sorted_node_ids[:top_n]:
        final_results.append(NodeWithScore(node=nodes_map[node_id], score=fused_scores[node_id]))

    return final_results
