from __future__ import annotations

import asyncio
import os
from typing import Any

from state.tools.db_initializer import DBInitializer
from tools.rag import RAGManager

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")
VECTOR_DOCSTORE_PATH = os.path.join(
    BASE_DIR,
    "knowledge_base",
    "indices",
    "vector",
    "docstore.json",
)


class RAGReadOnlyBridge:
    """主循环只读检索桥接层，不参与动作判定。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        auto_initialize: bool = True,
    ):
        """
        功能：初始化对象状态与依赖。
        入参：enabled；auto_initialize。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.enabled = enabled
        self.auto_initialize = auto_initialize
        self._manager: RAGManager | None = None

    def _ensure_sqlite(self) -> None:
        """
        功能：执行 `_ensure_sqlite` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if os.path.exists(DB_PATH):
            return
        DBInitializer(DB_PATH).initialize_db()

    def _ensure_manager(self) -> RAGManager:
        """
        功能：执行 `_ensure_manager` 相关业务逻辑。
        入参：无。
        出参：RAGManager。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self._manager is None:
            if self.auto_initialize:
                self._ensure_sqlite()
            self._manager = RAGManager()
            if self.auto_initialize and not os.path.exists(VECTOR_DOCSTORE_PATH):
                self._manager.update_index()
        return self._manager

    def build_snapshot(self, query: str) -> dict[str, Any]:
        """
        功能：构建并返回所需结构或结果。
        入参：query。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not self.enabled:
            return {
                "rag_enabled": False,
                "rag_ready": False,
                "rag_query": query,
                "rag_context": "",
                "rag_error": "RAG 只读桥接已禁用",
            }

        try:
            manager = self._ensure_manager()
            context = manager.query_lore_readonly(query)
            return {
                "rag_enabled": True,
                "rag_ready": True,
                "rag_query": query,
                "rag_context": str(context),
                "rag_error": "",
            }
        except Exception as error:  # noqa: BLE001
            return {
                "rag_enabled": True,
                "rag_ready": False,
                "rag_query": query,
                "rag_context": "",
                "rag_error": str(error),
            }

    async def build_snapshot_async(self, query: str) -> dict[str, Any]:
        """
        功能：在线程池中执行只读检索，避免阻塞主循环事件循环。
        入参：query。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await asyncio.to_thread(self.build_snapshot, query)
