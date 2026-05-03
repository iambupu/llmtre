from __future__ import annotations

import sqlite3
from typing import Any

import pytest

import tools.rag.routers.sql_router as sql_router
from tools.rag.routers.sql_router import SQLRouter


class _FakeNLSQLTableQueryEngine:
    """
    功能：替代真实 NLSQLTableQueryEngine，记录构造参数并避免触发 LLM 查询链路。
    入参：**kwargs：生产构造参数。
    出参：_FakeNLSQLTableQueryEngine。
    异常：无显式异常。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _init_state_db(db_path) -> None:
    """
    功能：创建 SQLRouter 需要的最小 Active 表结构和样例数据。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：sqlite 建表/写入失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE entities_active (entity_id TEXT PRIMARY KEY, name TEXT, hp INTEGER)"
        )
        connection.execute(
            "INSERT INTO entities_active(entity_id, name, hp) VALUES ('player_01', '玩家', 100)"
        )
        connection.commit()


def test_sql_router_rejects_missing_database(tmp_path) -> None:
    """
    功能：验证数据库文件缺失时初始化会抛出 FileNotFoundError。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 DB 缺失降级提示回归。
    """
    router = SQLRouter(str(tmp_path / "missing.db"))

    with pytest.raises(FileNotFoundError, match="数据库文件不存在"):
        router.query_raw("SELECT 1")


def test_sql_router_rejects_database_without_target_tables(tmp_path, caplog) -> None:
    """
    功能：验证 SQLite 存在但缺少目标表时初始化失败并记录错误日志。
    入参：tmp_path；caplog。
    出参：None。
    异常：断言失败表示缺表降级或日志证据回归。
    """
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
        connection.commit()
    router = SQLRouter(str(db_path))
    caplog.set_level("ERROR", logger="RAGManager.SQLRouter")

    with pytest.raises(RuntimeError, match="SQL 路由可用表为空"):
        router.query_raw("SELECT 1")
    assert "SQL 数据库反射失败" in caplog.text


def test_sql_router_query_raw_allows_select_and_returns_rows(tmp_path) -> None:
    """
    功能：验证 query_raw 只读 SELECT 查询返回字典行，并缓存可用表结构。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示只读查询主路径回归。
    """
    db_path = tmp_path / "state.db"
    _init_state_db(db_path)
    router = SQLRouter(str(db_path))

    rows = router.query_raw(
        "SELECT entity_id, hp FROM entities_active WHERE entity_id = 'player_01'"
    )

    assert rows == [{"entity_id": "player_01", "hp": 100}]
    assert router.available_tables == ["entities_active"]


def test_sql_router_query_raw_rejects_write_sql(tmp_path) -> None:
    """
    功能：验证 query_raw 拒绝非 SELECT/PRAGMA 的写 SQL，避免 RAG 只读入口产生副作用。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示非法查询拒绝策略回归。
    """
    db_path = tmp_path / "state.db"
    _init_state_db(db_path)
    router = SQLRouter(str(db_path))

    with pytest.raises(ValueError, match="SQLRouter 仅允许只读查询"):
        router.query_raw("DELETE FROM entities_active")


def test_sql_router_get_query_engine_handles_missing_llm_and_valid_llm(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证自然语言 SQL 引擎在 LLM 缺失时返回 None，有 LLM 时按可用表创建查询引擎。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示只读查询引擎创建或 LLM 缺失降级回归。
    """
    db_path = tmp_path / "state.db"
    _init_state_db(db_path)
    router = SQLRouter(str(db_path))
    monkeypatch.setattr(sql_router, "NLSQLTableQueryEngine", _FakeNLSQLTableQueryEngine)
    caplog.set_level("WARNING", logger="RAGManager.SQLRouter")

    assert router.get_query_engine(None) is None
    engine = router.get_query_engine(object())

    assert "未配置 LLM，无法使用 NLSQLTableQueryEngine" in caplog.text
    assert isinstance(engine, _FakeNLSQLTableQueryEngine)
    assert engine.kwargs["tables"] == ["entities_active"]
