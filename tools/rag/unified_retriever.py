import logging
import os
from typing import Any

from llama_index.core import (
    Settings,
    StorageContext,
    load_index_from_storage,
)
from llama_index.core.query_engine import RetrieverQueryEngine, RouterQueryEngine
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.selectors import LLMSingleSelector
from llama_index.core.tools import QueryEngineTool
from llama_index.retrievers.bm25 import BM25Retriever

from .fusions.rrf_fusion import reciprocal_rank_fusion
from .routers.sql_router import SQLRouter

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")
INDEX_DIR = os.path.join(BASE_DIR, "knowledge_base", "indices")
VECTOR_INDEX_DIR = os.path.join(INDEX_DIR, "vector")
GRAPH_INDEX_DIR = os.path.join(INDEX_DIR, "graph")

logger = logging.getLogger("RAGManager.Retriever")

class HybridRetriever(BaseRetriever):
    """自定义混合检索器：整合 Vector, BM25, Graph"""

    def __init__(
        self,
        vector_retriever: BaseRetriever,
        bm25_retriever: BaseRetriever,
        graph_retriever: BaseRetriever | None = None
    ):
        """
        功能：初始化对象状态与依赖。
        入参：vector_retriever；bm25_retriever；graph_retriever。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self._vector_retriever = vector_retriever
        self._bm25_retriever = bm25_retriever
        self._graph_retriever = graph_retriever
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        """
        功能：执行多路召回并执行 RRF 融合。
        入参：query_bundle。
        出参：List[NodeWithScore]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        results = []

        # 1. 向量检索
        results.append(self._vector_retriever.retrieve(query_bundle))

        # 2. 关键字检索 (BM25)
        results.append(self._bm25_retriever.retrieve(query_bundle))

        # 3. 属性图谱检索
        if self._graph_retriever:
            try:
                results.append(self._graph_retriever.retrieve(query_bundle))
            except Exception as e:
                logger.warning(f"图谱检索失败: {e}")

        # 4. 执行倒数秩融合 (RRF)
        fused_nodes = reciprocal_rank_fusion(results, top_n=5)
        return fused_nodes


class UnifiedRetriever:
    """统一查询入口：整合 Vector, BM25, PropertyGraph 以及 SQL 实时状态检索"""

    def __init__(self, config: dict[str, Any]):
        """
        功能：初始化对象状态与依赖。
        入参：config。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.config = config
        self._vector_index: Any | None = None
        self._graph_index: Any | None = None
        self._sql_router: SQLRouter | None = None
        self._hybrid_retriever: HybridRetriever | None = None
        self._hybrid_cache_key: tuple[int, bool] | None = None
        self._load_indices()

    def _get_sql_router(self) -> SQLRouter:
        """
        功能：按需初始化 SQL 路由。
        入参：无。
        出参：SQLRouter。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self._sql_router is None:
            self._sql_router = SQLRouter(DB_PATH)
        return self._sql_router

    def _load_indices(self) -> None:
        """
        功能：加载持久化索引。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # 加载向量索引
        if os.path.exists(os.path.join(VECTOR_INDEX_DIR, "docstore.json")):
            storage_context = StorageContext.from_defaults(persist_dir=VECTOR_INDEX_DIR)
            self._vector_index = load_index_from_storage(storage_context)
            logger.info("向量索引加载成功。")

        # 加载图谱索引
        if os.path.exists(os.path.join(GRAPH_INDEX_DIR, "graph_store.json")):
            try:
                storage_context = StorageContext.from_defaults(persist_dir=GRAPH_INDEX_DIR)
                self._graph_index = load_index_from_storage(storage_context)
                logger.info("属性图谱索引加载成功。")
            except Exception as e:
                logger.warning(f"无法加载图谱索引: {e}")

    def query(self, query_str: str) -> str:
        """
        功能：对外暴露的统一查询方法，包含自动路由逻辑。
        入参：query_str。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not self._vector_index:
            return "检索失败：向量库尚未初始化。"

        # 1. 准备混合 RAG 查询引擎（缓存复用，避免每次全量重建）。
        hybrid_retriever = self._get_hybrid_retriever()
        rag_engine = RetrieverQueryEngine.from_args(
            retriever=hybrid_retriever,
            response_mode=ResponseMode.COMPACT,
        )

        # 2. 准备 SQL 查询引擎 (按需逻辑)
        if Settings.llm is None:
            logger.info("未配置 LLM，执行纯向量检索回退模式...")
            nodes = hybrid_retriever.retrieve(query_str)
            return "\n\n---\n\n".join([n.node.get_content() for n in nodes])

        sql_engine = self._get_sql_router().get_query_engine(Settings.llm)

        # 3. 构建路由引擎
        rag_tool = QueryEngineTool.from_defaults(
            query_engine=rag_engine,
            description="用于查询游戏规则、物理法则、世界观设定、人物背景和历史传说。"
        )

        tools = [rag_tool]
        if sql_engine:
            sql_tool = QueryEngineTool.from_defaults(
                query_engine=sql_engine,
                description="用于查询玩家或NPC的实时数值状态（血量HP、法力MP、属性）、背包物品库存、全局开关状态以及最近发生的历史事件日志。"
            )
            tools.append(sql_tool)

        router_engine = RouterQueryEngine(
            selector=LLMSingleSelector.from_defaults(),
            query_engine_tools=tools,
            verbose=True
        )

        # 4. 执行路由查询
        logger.info(f"正在路由查询意图: {query_str}")
        try:
            response = router_engine.query(query_str)
            return str(response)
        except Exception as e:
            logger.error(f"路由查询失败: {e}")
            # 如果路由失败 (例如 DB 未就绪), 回退到纯 RAG
            logger.info("路由失败，尝试回退到纯 RAG 检索...")
            return str(rag_engine.query(query_str))

    def query_readonly(self, query_str: str) -> str:
        """
        功能：只读检索模式：不走路由与生成，仅返回融合召回片段。
        入参：query_str。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not self._vector_index:
            return "检索失败：向量库尚未初始化。"

        hybrid_retriever = self._get_hybrid_retriever()
        fused_nodes = hybrid_retriever.retrieve(query_str)
        if not fused_nodes:
            return ""
        return "\n\n---\n\n".join([n.node.get_content() for n in fused_nodes])

    def _build_hybrid_cache_key(self) -> tuple[int, bool]:
        """
        功能：基于当前索引快照生成检索器缓存键。
        入参：无。
        出参：tuple[int, bool]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self._vector_index is None:
            return (0, bool(self._graph_index))
        nodes = list(self._vector_index.docstore.docs.values())
        return (len(nodes), bool(self._graph_index))

    def _build_hybrid_retriever(self) -> HybridRetriever:
        """
        功能：构建混合检索器实例。
        入参：无。
        出参：HybridRetriever。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self._vector_index is None:
            raise RuntimeError("向量索引未初始化，无法构建混合检索器。")
        vector_retriever = self._vector_index.as_retriever(similarity_top_k=5)
        nodes = list(self._vector_index.docstore.docs.values())
        bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=5)
        graph_retriever: BaseRetriever | None = None
        if self._graph_index:
            try:
                graph_retriever = self._graph_index.as_retriever(similarity_top_k=3)
            except Exception as error:  # noqa: BLE001
                logger.warning(f"图谱检索器初始化失败: {error}")
        return HybridRetriever(vector_retriever, bm25_retriever, graph_retriever)

    def _get_hybrid_retriever(self) -> HybridRetriever:
        """
        功能：按缓存键懒加载并复用混合检索器。
        入参：无。
        出参：HybridRetriever。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        cache_key = self._build_hybrid_cache_key()
        if self._hybrid_retriever is None or self._hybrid_cache_key != cache_key:
            self._hybrid_retriever = self._build_hybrid_retriever()
            self._hybrid_cache_key = cache_key
            logger.info("混合检索器已构建: key=%s", cache_key)
        return self._hybrid_retriever
