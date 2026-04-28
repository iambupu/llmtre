from llama_index.core.schema import NodeWithScore


def reciprocal_rank_fusion(
    results: list[list[NodeWithScore]],
    k: int = 60,
    top_n: int = 5
) -> list[NodeWithScore]:
    """
    功能：Reciprocal Rank Fusion (RRF) 算法实现。
    入参：results；k；top_n。
    出参：List[NodeWithScore]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    fused_scores = {}
    nodes_map = {}

    for retriever_results in results:
        for rank, node_with_score in enumerate(retriever_results):
            node_id = node_with_score.node.node_id
            nodes_map[node_id] = node_with_score.node

            # RRF Score = 1 / (rank + k)
            if node_id not in fused_scores:
                fused_scores[node_id] = 0.0
            fused_scores[node_id] += 1.0 / (rank + k)

    # 排序
    sorted_node_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)

    # 转换为 NodeWithScore 列表
    final_results = []
    for node_id in sorted_node_ids[:top_n]:
        final_results.append(NodeWithScore(
            node=nodes_map[node_id],
            score=fused_scores[node_id]
        ))

    return final_results
