import logging
import os
from typing import Any

from llama_index.core import SQLDatabase
from llama_index.core.query_engine import NLSQLTableQueryEngine
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("RAGManager.SQLRouter")

class SQLRouter:
    """SQL 路由：将自然语言转换为针对 SQLite 数据库的查询"""

    def __init__(self, db_path: str):
        """
        功能：初始化对象状态与依赖。
        入参：db_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path
        # 延迟初始化标志
        self._engine: Engine | None = None
        self._sql_database: SQLDatabase | None = None
        self.available_tables: list[str] = []
        # 适配 Active/Shadow 架构的表名
        self.target_tables = [
            "entities_active",
            "items",
            "inventory_active",
            "world_state_active",
            "quests_active",
            "event_logs"
        ]

    def _ensure_initialized(self) -> None:
        """
        功能：仅在真正查询时初始化数据库连接和元数据反射。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self._engine is None:
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(f"数据库文件不存在: {self.db_path}. 请先运行初始化。")

            self._engine = create_engine(
                f"sqlite:///{self.db_path}",
                connect_args={"check_same_thread": False},
            )
            try:
                inspector = inspect(self._engine)
                existing_tables = set(inspector.get_table_names())
                self.available_tables = [t for t in self.target_tables if t in existing_tables]
                if not self.available_tables:
                    raise RuntimeError("SQL 路由可用表为空，无法初始化查询引擎。")

                self._sql_database = SQLDatabase(self._engine, include_tables=self.available_tables)
                logger.info(f"SQL 数据库元数据加载成功，监控表: {self.available_tables}")
            except Exception as e:
                logger.error(f"SQL 数据库反射失败 (表结构可能不完整): {e}")
                raise

    def get_query_engine(self, llm: Any) -> NLSQLTableQueryEngine | None:
        """
        功能：获取基于自然语言的 SQL 查询引擎。
        入参：llm。
        出参：NLSQLTableQueryEngine。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if llm is None:
            logger.warning("未配置 LLM，无法使用 NLSQLTableQueryEngine。")
            return None

        self._ensure_initialized()
        if self._sql_database is None:
            return None
        return NLSQLTableQueryEngine(
            sql_database=self._sql_database,
            llm=llm,
            tables=self.available_tables
        )

    def query_raw(self, sql: str) -> list[dict[str, Any]]:
        """
        功能：执行原始 SQL 查询。
        入参：sql。
        出参：List[Dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self._ensure_initialized()
        if self._engine is None:
            return []
        with self._engine.connect() as connection:
            result = connection.execute(text(sql))
            return [dict(row) for row in result.mappings().all()]
