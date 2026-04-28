import logging
import os

from llama_index.core import VectorStoreIndex
from llama_index.core.schema import BaseNode

logger = logging.getLogger("RAGManager.VectorBuilder")

class VectorIndexBuilder:
    """向量索引专用构建器"""

    def __init__(self, index_dir: str):
        """
        功能：初始化对象状态与依赖。
        入参：index_dir。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.index_dir = os.path.join(index_dir, "vector")

    def build(self, nodes: list[BaseNode]) -> VectorStoreIndex:
        """
        功能：根据节点列表构建向量索引并持久化。
        入参：nodes。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(f"正在构建向量索引，切片总数: {len(nodes)}")
        try:
            index = VectorStoreIndex(nodes)
            os.makedirs(self.index_dir, exist_ok=True)
            index.storage_context.persist(persist_dir=self.index_dir)
            logger.info(f"[OK] 向量索引已持久化至: {self.index_dir}")
            return index
        except Exception as e:
            logger.error(f"向量索引构建失败: {e}")
            raise e
