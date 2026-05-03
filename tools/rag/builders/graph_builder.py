import logging
import os
from typing import Any

from llama_index.core import Document, PropertyGraphIndex, Settings
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor

logger = logging.getLogger("RAGManager.GraphBuilder")

class PropertyGraphIndexBuilder:
    """属性图谱专用构建器"""

    def __init__(self, index_dir: str, config: dict[str, Any]):
        """
        功能：初始化对象状态与依赖。
        入参：index_dir；config。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.index_dir = os.path.join(index_dir, "graph")
        self.config = config.get("property_graph", {})

    def build(self, documents: list[Document]) -> PropertyGraphIndex | None:
        """
        功能：根据文档内容提取关系并构建图谱索引。
        入参：documents。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not self.config.get("enabled", False):
            logger.info("属性图谱构建已禁用，跳过。")
            return None

        if not Settings.llm:
            logger.warning("未配置 LLM，无法进行属性图谱关系提取，跳过。")
            return None

        logger.info("正在执行实体关系抽取 (Property Graph Construction)...")

        # 从配置中加载自定义 Prompt
        extraction_prompt = self.config.get(
            "extraction_prompt",
            "提取实体和关系: (实体1, 关系, 实体2)\n文本: {text}",
        )

        kg_extractor = SchemaLLMPathExtractor(
            llm=Settings.llm,
            extract_prompt=extraction_prompt,
        )

        try:
            graph_index = PropertyGraphIndex.from_documents(
                documents,
                kg_extractors=[kg_extractor],
                show_progress=True,
            )
            os.makedirs(self.index_dir, exist_ok=True)
            graph_index.storage_context.persist(persist_dir=self.index_dir)
            logger.info(f"[OK] 属性图谱已持久化至: {self.index_dir}")
            return graph_index
        except Exception as e:
            # 降级路径：属性图谱是增强索引。失败时保留向量索引构建，
            # 避免验收把可选能力误判为主链路错误。
            logger.warning(f"属性图谱构建失败，已降级跳过: {e}")
            return None
