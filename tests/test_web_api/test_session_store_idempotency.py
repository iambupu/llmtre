from __future__ import annotations

import sqlite3

import pytest

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.session_store import WebSessionStore


def _init_runtime_db(db_path: str) -> None:
    """
    功能：初始化 Web 运行时表结构，供会话存储层回归测试使用。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def test_persist_turn_result_with_idempotency_rolls_back_on_builder_error(tmp_path) -> None:
    """
    功能：验证响应构建失败时事务回滚，不会留下“已落盘未缓存”的脏状态。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示幂等原子性被破坏。
    """
    db_path = str(tmp_path / "runtime.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_atomic01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )

    with pytest.raises(RuntimeError, match="builder failed"):
        store.persist_turn_result_with_idempotency(
            scope="create_turn",
            session_id="sess_atomic01",
            request_id="req_atomic_01",
            user_input="观察周围",
            turn_result={
                "is_valid": True,
                "action_intent": {"type": "observe"},
                "physics_diff": {},
                "final_response": "ok",
                "is_sandbox_mode": False,
            },
            memory_summary="",
            now_iso="2026-05-01T00:00:01Z",
            response_builder=lambda _turn_id: (_ for _ in ()).throw(RuntimeError("builder failed")),
        )

    with sqlite3.connect(db_path) as connection:
        turn_count = int(connection.execute("SELECT COUNT(1) FROM web_session_turns").fetchone()[0])
        idem_count = int(
            connection.execute(
                "SELECT COUNT(1) FROM web_idempotency_keys",
            ).fetchone()[0]
        )
        current_turn_id = int(
            connection.execute(
                "SELECT current_turn_id FROM web_sessions WHERE session_id = ?",
                ("sess_atomic01",),
            ).fetchone()[0]
        )

    assert turn_count == 0
    assert idem_count == 0
    assert current_turn_id == 0


def test_create_session_with_idempotency_is_atomic_on_replay(tmp_path) -> None:
    """
    功能：验证 create_session 幂等事务在重放时不会重复创建会话，也不会产生孤儿会话。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 create_session 幂等原子性退化。
    """
    db_path = str(tmp_path / "runtime_create_session_idem.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    payload = {
        "session_id": "sess_atomic_created_01",
        "character_id": "player_01",
        "sandbox_mode": False,
        "current_session_turn_id": 0,
        "created_at": "2026-05-01T00:00:00Z",
    }
    first_payload, first_created = store.create_session_with_idempotency(
        scope="create_session",
        request_id="req_create_atomic_01",
        session_id="sess_atomic_created_01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        response_payload=payload,
    )
    second_payload, second_created = store.create_session_with_idempotency(
        scope="create_session",
        request_id="req_create_atomic_01",
        session_id="sess_atomic_created_02",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:01Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        response_payload={**payload, "session_id": "sess_atomic_created_02"},
    )
    assert first_created is True
    assert second_created is False
    assert first_payload == second_payload
    with sqlite3.connect(db_path) as connection:
        session_count = int(connection.execute("SELECT COUNT(1) FROM web_sessions").fetchone()[0])
        cached_session_id = str(
            connection.execute(
                """
                SELECT session_id
                FROM web_sessions
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()[0]
        )
    assert session_count == 1
    assert cached_session_id == "sess_atomic_created_01"


def test_persist_turn_result_with_idempotency_returns_cached_payload_on_replay(tmp_path) -> None:
    """
    功能：验证同 scope/session/request 重放时命中缓存，不重复推进会话回合。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示幂等重放语义退化。
    """
    db_path = str(tmp_path / "runtime_replay.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_replay01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    turn_result = {
        "is_valid": True,
        "action_intent": {"type": "observe"},
        "physics_diff": {},
        "final_response": "ok",
        "is_sandbox_mode": False,
    }
    first_payload, first_created = store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id="sess_replay01",
        request_id="req_replay_01",
        user_input="观察周围",
        turn_result=turn_result,
        memory_summary="m1",
        now_iso="2026-05-01T00:00:01Z",
        response_builder=lambda persisted_turn_id: {
            "session_id": "sess_replay01",
            "session_turn_id": persisted_turn_id,
            "request_id": "req_replay_01",
            "final_response": "ok",
        },
    )
    second_payload, second_created = store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id="sess_replay01",
        request_id="req_replay_01",
        user_input="观察周围",
        turn_result=turn_result,
        memory_summary="m2",
        now_iso="2026-05-01T00:00:02Z",
        response_builder=lambda persisted_turn_id: {
            "session_id": "sess_replay01",
            "session_turn_id": persisted_turn_id,
            "request_id": "req_replay_01",
            "final_response": "should_not_be_used",
        },
    )
    assert first_created is True
    assert second_created is False
    assert first_payload == second_payload
    with sqlite3.connect(db_path) as connection:
        turn_count = int(
            connection.execute(
                "SELECT COUNT(1) FROM web_session_turns WHERE session_id = ?",
                ("sess_replay01",),
            ).fetchone()[0]
        )
        current_turn_id = int(
            connection.execute(
                "SELECT current_turn_id FROM web_sessions WHERE session_id = ?",
                ("sess_replay01",),
            ).fetchone()[0]
        )
    assert turn_count == 1
    assert current_turn_id == 1


def test_web_session_turns_request_id_unique_index_blocks_duplicate_writes(tmp_path) -> None:
    """
    功能：验证底层 `(session_id, request_id)` 唯一索引生效，防止绕过幂等接口时重复写入。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：期望第二次直接写入抛出 sqlite3.IntegrityError；未抛出表示唯一约束退化。
    """
    db_path = str(tmp_path / "runtime_unique_request.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_unique_req",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    turn_result = {
        "is_valid": True,
        "action_intent": {"type": "observe"},
        "physics_diff": {},
        "final_response": "ok",
        "is_sandbox_mode": False,
    }
    first_id = store.persist_turn_result(
        session_id="sess_unique_req",
        request_id="req_unique_01",
        user_input="观察周围",
        turn_result=turn_result,
        memory_summary="m1",
        now_iso="2026-05-01T00:00:01Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.persist_turn_result(
            session_id="sess_unique_req",
            request_id="req_unique_01",
            user_input="再次观察",
            turn_result=turn_result,
            memory_summary="m2",
            now_iso="2026-05-01T00:00:02Z",
        )
    with sqlite3.connect(db_path) as connection:
        turn_count = int(
            connection.execute(
                """
                SELECT COUNT(1)
                FROM web_session_turns
                WHERE session_id = ? AND request_id = ?
                """,
                ("sess_unique_req", "req_unique_01"),
            ).fetchone()[0]
        )
        current_turn_id = int(
            connection.execute(
                "SELECT current_turn_id FROM web_sessions WHERE session_id = ?",
                ("sess_unique_req",),
            ).fetchone()[0]
        )
    assert first_id == 1
    assert turn_count == 1
    assert current_turn_id == 1


def test_get_idempotent_response_returns_none_for_non_object_payload(tmp_path) -> None:
    """
    功能：验证幂等缓存若为非对象 JSON（历史脏数据）时按未命中返回 None。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示缓存降级契约被破坏。
    """
    db_path = str(tmp_path / "runtime_non_object_payload.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
            VALUES(?, ?, ?, ?)
            """,
            ("create_turn", "sess_nonobj", "req_nonobj", "\"ok\""),
        )
        connection.commit()

    assert store.get_idempotent_response("create_turn", "sess_nonobj", "req_nonobj") is None


def test_persist_turn_result_with_idempotency_ignores_non_object_cache_and_persists(
    tmp_path,
) -> None:
    """
    功能：验证幂等缓存命中但为脏值时不会提前返回，而是继续落盘新回合并覆写缓存。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示幂等降级路径行为退化。
    """
    db_path = str(tmp_path / "runtime_non_object_rewrite.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_nonobj_rewrite",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
            VALUES(?, ?, ?, ?)
            """,
            ("create_turn", "sess_nonobj_rewrite", "req_nonobj_rewrite", "\"stale\""),
        )
        connection.commit()

    payload, created = store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id="sess_nonobj_rewrite",
        request_id="req_nonobj_rewrite",
        user_input="继续前进",
        turn_result={
            "is_valid": True,
            "action_intent": {"type": "move"},
            "physics_diff": {"hp": -1},
            "final_response": "你向前一步。",
            "is_sandbox_mode": False,
        },
        memory_summary="m_new",
        now_iso="2026-05-01T00:00:01Z",
        response_builder=lambda persisted_turn_id: {
            "session_id": "sess_nonobj_rewrite",
            "session_turn_id": persisted_turn_id,
            "request_id": "req_nonobj_rewrite",
            "final_response": "你向前一步。",
        },
    )
    assert created is True
    assert payload["session_turn_id"] == 1
    with sqlite3.connect(db_path) as connection:
        turn_count = int(connection.execute("SELECT COUNT(1) FROM web_session_turns").fetchone()[0])
        response_json = str(
            connection.execute(
                """
                SELECT response_json
                FROM web_idempotency_keys
                WHERE scope = ? AND session_id = ? AND request_id = ?
                """,
                ("create_turn", "sess_nonobj_rewrite", "req_nonobj_rewrite"),
            ).fetchone()[0]
        )
    assert turn_count == 1
    assert "\"session_turn_id\": 1" in response_json


def test_persist_turn_result_raises_when_session_missing(tmp_path) -> None:
    """
    功能：验证会话不存在时写回合会抛出完整性异常，防止无主回合写入。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：期望 sqlite3.IntegrityError；未抛出表示约束退化。
    """
    db_path = str(tmp_path / "runtime_missing_session.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)

    with pytest.raises(sqlite3.IntegrityError, match="session_id 不存在"):
        store.persist_turn_result(
            session_id="sess_missing",
            request_id="req_missing_01",
            user_input="观察四周",
            turn_result={
                "is_valid": False,
                "action_intent": None,
                "physics_diff": None,
                "final_response": "未找到会话",
                "is_sandbox_mode": False,
            },
            memory_summary="",
            now_iso="2026-05-01T00:00:01Z",
        )


def test_update_memory_fields_and_get_session_none_branch(tmp_path) -> None:
    """
    功能：验证不存在会话时更新摘要/策略无异常，且查询缺失会话返回 None。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示无副作用分支行为异常。
    """
    db_path = str(tmp_path / "runtime_update_none.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.update_memory_policy(
        "sess_missing",
        {"mode": "auto", "max_turns": 3},
        "2026-05-01T00:00:01Z",
    )
    store.update_memory_summary("sess_missing", "summary", "2026-05-01T00:00:01Z")
    assert store.get_session("sess_missing") is None
    assert store.get_turn("sess_missing", 1) is None
    total, items = store.list_turns("sess_missing", page=1, page_size=20)
    assert total == 0
    assert items == []


def test_clear_session_turns_and_reset_branches(tmp_path) -> None:
    """
    功能：验证 reset 在会话不存在时返回 False；存在会话且不保留角色时重置为 player_01。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示重置事务边界或分支契约退化。
    """
    db_path = str(tmp_path / "runtime_clear_reset.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    assert (
        store.clear_session_turns_and_reset(
            "sess_not_exists",
            keep_character=True,
            now_iso="2026-05-01T00:00:01Z",
        )
        is False
    )
    store.create_session(
        session_id="sess_reset01",
        character_id="npc_02",
        sandbox_mode=True,
        now_iso="2026-05-01T00:00:02Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    persisted_turn_id = store.persist_turn_result(
        session_id="sess_reset01",
        request_id="req_reset_01",
        user_input="查看地图",
        turn_result={
            "is_valid": True,
            "action_intent": {"type": "observe"},
            "physics_diff": {},
            "final_response": "地图已展开。",
            "is_sandbox_mode": True,
        },
        memory_summary="m_before_reset",
        now_iso="2026-05-01T00:00:03Z",
    )
    assert persisted_turn_id == 1
    assert (
        store.clear_session_turns_and_reset(
            "sess_reset01",
            keep_character=False,
            now_iso="2026-05-01T00:00:04Z",
        )
        is True
    )
    session = store.get_session("sess_reset01")
    assert session is not None
    assert session["character_id"] == "player_01"
    assert session["current_turn_id"] == 0
    assert session["sandbox_mode"] is False
    total, items = store.list_turns("sess_reset01", page=1, page_size=20)
    assert total == 0
    assert items == []
