"""
功能：覆盖 session store idempotency 的回归测试。
"""

from __future__ import annotations

import sqlite3

import pytest

from state.contracts.memory import NarrativeMemoryItem
from state.tools.runtime_schema import ensure_runtime_tables
from web_api.narrative_memory import build_narrative_memory_items
from web_api.session_store import WebSessionStore, build_session_runtime_character_id


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


def _init_runtime_db_with_character_state(db_path: str) -> None:
    """
    功能：初始化包含实体/背包表的 Web runtime 数据库，供 session 状态隔离测试使用。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities_active (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER,
                max_hp INTEGER,
                mp INTEGER,
                max_mp INTEGER,
                current_location_id TEXT,
                state_flags_json TEXT,
                updated_at TEXT
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory_active (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER,
                UNIQUE(owner_id, item_id)
            )
            """)
        cursor.execute("""
            INSERT INTO entities_active(
                entity_id, hp, max_hp, mp, max_mp, current_location_id, state_flags_json, updated_at
            )
            VALUES('player_01', 100, 100, 50, 50, 'town_square', '[]', 'seed')
            """)
        cursor.execute("""
            INSERT INTO inventory_active(owner_id, item_id, quantity)
            VALUES('player_01', 'starter_potion', 2)
            """)
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


def test_persist_turn_result_updates_session_progress_from_scene_snapshot(tmp_path) -> None:
    """
    功能：验证回合持久化会从 scene_snapshot 同步会话列表使用的当前场景进度。
    入参：tmp_path（pytest fixture）：临时数据库目录。
    出参：None。
    异常：断言失败表示玩家存档选择器可能继续显示过期场景。
    """
    db_path = str(tmp_path / "runtime_progress.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_progress01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE web_sessions
            SET session_metadata_json = ?
            WHERE session_id = ?
            """,
            (
                '{"current_scene_id":"ferry_landing","current_scene_title":"鹭潮渡口"}',
                "sess_progress01",
            ),
        )
        connection.commit()

    payload, created = store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id="sess_progress01",
        request_id="req_progress_01",
        user_input="沿退潮露出的石梁走向晓桥",
        turn_result={
            "is_valid": True,
            "action_intent": {"type": "move", "target_id": "dawn_causeway"},
            "physics_diff": {},
            "final_response": "玩家沿退潮露出的石梁走向晓桥。",
            "is_sandbox_mode": False,
            "scene_snapshot": {
                "current_location": {"id": "dawn_causeway", "name": "晓桥"},
            },
            "quest_updates": [
                {"quest_id": "recover_the_tide_oath", "status": "completed"},
            ],
        },
        memory_summary="第1回合：玩家抵达晓桥。",
        now_iso="2026-05-01T00:00:01Z",
        response_builder=lambda persisted_turn_id: {
            "session_id": "sess_progress01",
            "session_turn_id": persisted_turn_id,
        },
    )

    summaries = store.list_sessions(limit=10)
    session = store.get_session("sess_progress01")

    assert created is True
    assert payload["session_turn_id"] == 1
    assert summaries[0]["current_scene_id"] == "dawn_causeway"
    assert summaries[0]["current_scene_title"] == "晓桥"
    assert session is not None
    assert session["session_metadata"]["current_scene_id"] == "dawn_causeway"
    assert session["session_metadata"]["current_scene_title"] == "晓桥"


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
        cached_session_id = str(connection.execute("""
                SELECT session_id
                FROM web_sessions
                ORDER BY created_at ASC
                LIMIT 1
                """).fetchone()[0])
    assert session_count == 1
    assert cached_session_id == "sess_atomic_created_01"


def test_create_and_reset_session_use_isolated_runtime_character(tmp_path) -> None:
    """
    功能：验证每个 session 会克隆独立运行角色，reset 会把运行角色恢复为基准快照。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 session 间 HP/位置/背包状态仍会互相污染。
    """
    db_path = str(tmp_path / "runtime_session_isolation.db")
    _init_runtime_db_with_character_state(db_path)
    store = WebSessionStore(db_path)
    runtime_a = build_session_runtime_character_id("sess_iso_a01")
    runtime_b = build_session_runtime_character_id("sess_iso_b01")
    for session_id, runtime_character_id in (
        ("sess_iso_a01", runtime_a),
        ("sess_iso_b01", runtime_b),
    ):
        store.create_session(
            session_id=session_id,
            character_id="player_01",
            runtime_character_id=runtime_character_id,
            sandbox_mode=False,
            now_iso="2026-06-09T00:00:00Z",
            memory_policy={"mode": "auto", "max_turns": 20},
        )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE entities_active
            SET hp = 25, current_location_id = 'danger_room'
            WHERE entity_id = ?
            """,
            (runtime_a,),
        )
        connection.execute(
            """
            UPDATE inventory_active
            SET quantity = 1
            WHERE owner_id = ? AND item_id = 'starter_potion'
            """,
            (runtime_a,),
        )
        connection.commit()
        base = connection.execute(
            "SELECT hp, current_location_id FROM entities_active WHERE entity_id = 'player_01'"
        ).fetchone()
        other = connection.execute(
            "SELECT hp, current_location_id FROM entities_active WHERE entity_id = ?",
            (runtime_b,),
        ).fetchone()
        other_potion = connection.execute(
            """
            SELECT quantity FROM inventory_active
            WHERE owner_id = ? AND item_id = 'starter_potion'
            """,
            (runtime_b,),
        ).fetchone()

    assert base == (100, "town_square")
    assert other == (100, "town_square")
    assert other_potion == (2,)

    assert store.clear_session_turns_and_reset(
        session_id="sess_iso_a01",
        keep_character=True,
        now_iso="2026-06-09T00:00:01Z",
    )
    with sqlite3.connect(db_path) as connection:
        reset_row = connection.execute(
            "SELECT hp, current_location_id FROM entities_active WHERE entity_id = ?",
            (runtime_a,),
        ).fetchone()
        reset_potion = connection.execute(
            """
            SELECT quantity FROM inventory_active
            WHERE owner_id = ? AND item_id = 'starter_potion'
            """,
            (runtime_a,),
        ).fetchone()

    assert reset_row == (100, "town_square")
    assert reset_potion == (2,)


def test_create_session_with_initial_location_resets_runtime_progress_flags(tmp_path) -> None:
    """
    功能：验证 pack 新会话会把运行角色写到剧本起点，并清空基准角色遗留进度标记。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示新会话仍会继承上一局位置或线索状态。
    """
    db_path = str(tmp_path / "runtime_session_pack_start.db")
    _init_runtime_db_with_character_state(db_path)
    store = WebSessionStore(db_path)
    runtime_id = build_session_runtime_character_id("sess_pack_start01")
    with sqlite3.connect(db_path) as connection:
        connection.execute("""
            UPDATE entities_active
            SET hp = 31,
                mp = 7,
                current_location_id = 'ledgers_room',
                state_flags_json = '["ledger_second_boat_found"]'
            WHERE entity_id = 'player_01'
            """)
        connection.commit()

    store.create_session(
        session_id="sess_pack_start01",
        character_id="player_01",
        runtime_character_id=runtime_id,
        sandbox_mode=False,
        now_iso="2026-06-11T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        initial_location_id="ferry_landing",
    )

    with sqlite3.connect(db_path) as connection:
        runtime = connection.execute(
            """
            SELECT hp, mp, current_location_id, state_flags_json
            FROM entities_active
            WHERE entity_id = ?
            """,
            (runtime_id,),
        ).fetchone()
        base = connection.execute("""
            SELECT hp, mp, current_location_id, state_flags_json
            FROM entities_active
            WHERE entity_id = 'player_01'
            """).fetchone()

    assert runtime == (100, 50, "ferry_landing", "[]")
    assert base == (31, 7, "ledgers_room", '["ledger_second_boat_found"]')

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE entities_active
            SET current_location_id = 'dawn_causeway',
                state_flags_json = '["red_lantern_story_complete"]'
            WHERE entity_id = ?
            """,
            (runtime_id,),
        )
        connection.commit()

    assert store.clear_session_turns_and_reset(
        session_id="sess_pack_start01",
        keep_character=True,
        now_iso="2026-06-11T00:00:01Z",
        initial_location_id="ferry_landing",
    )
    with sqlite3.connect(db_path) as connection:
        reset_runtime = connection.execute(
            """
            SELECT hp, mp, current_location_id, state_flags_json
            FROM entities_active
            WHERE entity_id = ?
            """,
            (runtime_id,),
        ).fetchone()

    assert reset_runtime == (100, 50, "ferry_landing", "[]")


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
            ("create_turn", "sess_nonobj", "req_nonobj", '"ok"'),
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
            ("create_turn", "sess_nonobj_rewrite", "req_nonobj_rewrite", '"stale"'),
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
    assert '"session_turn_id": 1' in response_json


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


def test_narrative_memory_items_persist_format_and_reset(tmp_path) -> None:
    """
    功能：验证有效回合会写入结构化长期叙事记忆，并能格式化给 GM 上下文，reset 后清空。
    入参：tmp_path（pytest fixture）：临时数据库目录。
    出参：None。
    异常：断言失败表示长期记忆存储、导出或重置边界回归。
    """
    db_path = str(tmp_path / "runtime_narrative_memory.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_memory_items01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    turn_result = {
        "is_valid": True,
        "should_write_story_memory": True,
        "action_intent": {"type": "talk", "target_id": "ranger_ella", "parameters": {}},
        "physics_diff": {},
        "final_response": "艾拉压低声音，提醒玩家北侧石门需要银叶徽记。",
        "is_sandbox_mode": False,
        "scene_snapshot": {
            "current_location": {"id": "forest_edge", "name": "雾林边缘"},
        },
        "trigger_events": [
            {
                "trigger_id": "silver_leaf_hint",
                "type": "talk",
                "memory_text": "玩家得知北侧石门与银叶徽记有关。",
            }
        ],
        "quest_updates": [
            {
                "quest_id": "find_silver_leaf",
                "status": "active",
                "current_stage_id": "ask_ranger",
            }
        ],
    }

    payload, created = store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id="sess_memory_items01",
        request_id="req_memory_items01",
        user_input="询问艾拉石门的事",
        turn_result=turn_result,
        memory_summary="第1回合：玩家询问艾拉。",
        now_iso="2026-05-01T00:00:01Z",
        response_builder=lambda persisted_turn_id: {
            "session_id": "sess_memory_items01",
            "session_turn_id": persisted_turn_id,
        },
        memory_items_builder=lambda persisted_turn_id: build_narrative_memory_items(
            session_id="sess_memory_items01",
            session_turn_id=persisted_turn_id,
            user_input="询问艾拉石门的事",
            turn_result=turn_result,
        ),
    )

    assert created is True
    assert payload["session_turn_id"] == 1
    items = store.list_narrative_memory_items("sess_memory_items01", limit=10)
    context = store.build_narrative_memory_context("sess_memory_items01", limit=10)
    assert {item["kind"] for item in items} >= {"relationship", "unresolved_hook", "quest"}
    assert "艾拉压低声音" in context
    assert "银叶徽记" in context
    assert "find_silver_leaf" in context

    assert (
        store.clear_session_turns_and_reset(
            "sess_memory_items01",
            keep_character=True,
            now_iso="2026-05-01T00:00:02Z",
        )
        is True
    )
    assert store.list_narrative_memory_items("sess_memory_items01") == []


def test_narrative_memory_context_filters_by_current_scene_relevance(tmp_path) -> None:
    """
    功能：验证长期叙事记忆按当前地点、NPC、任务过滤，避免无关场景记忆污染 GM。
    入参：tmp_path（pytest fixture）：临时数据库目录。
    出参：None。
    异常：断言失败表示相关性过滤退化为会话级 TopN。
    """
    db_path = str(tmp_path / "runtime_memory_relevance.db")
    _init_runtime_db(db_path)
    store = WebSessionStore(db_path)
    store.create_session(
        session_id="sess_memory_relevance01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-01T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
    )
    memory_items = [
        _memory_item("mem_forest", "location", "forest_edge", "玩家在雾林边缘发现银叶刻痕。"),
        _memory_item("mem_city", "location", "far_city", "玩家曾在远城酒馆听见钟声。"),
        _memory_item("mem_ella", "npc", "ranger_ella", "艾拉仍在等待玩家归还银叶徽记。"),
        _memory_item("mem_guard", "npc", "gate_guard", "城门守卫怀疑玩家偷走通行证。"),
        _memory_item("mem_quest", "quest", "find_silver_leaf", "银叶徽记任务停在询问艾拉阶段。"),
        _memory_item("mem_other_quest", "quest", "repair_boat", "修船任务还缺一根桅杆。"),
        _memory_item(
            "mem_style",
            "session",
            "session",
            "玩家偏好先观察线索再与 NPC 对话。",
            kind="player_style",
        ),
    ]
    store.persist_turn_result(
        session_id="sess_memory_relevance01",
        request_id="req_memory_relevance01",
        user_input="准备继续调查",
        turn_result={
            "is_valid": True,
            "action_intent": {"type": "observe"},
            "physics_diff": {},
            "final_response": "你整理当前线索。",
            "is_sandbox_mode": False,
        },
        memory_summary="m1",
        now_iso="2026-05-01T00:00:01Z",
        memory_items_builder=lambda _turn_id: memory_items,
    )

    context = store.build_narrative_memory_context(
        "sess_memory_relevance01",
        limit=8,
        relevance={
            "location": {"forest_edge"},
            "npc": {"ranger_ella"},
            "character": {"ranger_ella"},
            "quest": {"find_silver_leaf"},
            "item": set(),
            "interaction": set(),
            "any": {"forest_edge", "ranger_ella", "find_silver_leaf"},
        },
    )

    assert "雾林边缘发现银叶刻痕" in context
    assert "艾拉仍在等待" in context
    assert "银叶徽记任务" in context
    assert "玩家偏好先观察" in context
    assert "远城酒馆" not in context
    assert "城门守卫" not in context
    assert "修船任务" not in context


def _memory_item(
    memory_id: str,
    subject_type: str,
    subject_id: str,
    text: str,
    *,
    kind: str = "discovery",
) -> NarrativeMemoryItem:
    """
    功能：构造测试用长期叙事记忆项。
    入参：memory_id/subject_type/subject_id/text/kind：记忆契约字段。
    出参：NarrativeMemoryItem。
    异常：字段非法时由 Pydantic 抛出 ValidationError，测试直接失败。
    """
    return NarrativeMemoryItem(
        memory_id=memory_id,
        session_id="sess_memory_relevance01",
        scope="session" if subject_type == "session" else subject_type,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=subject_id,
        text=text,
        evidence_turn_id=1,
        importance=5,
        confidence=1.0,
        status="active",
        metadata={},
        created_turn_id=1,
        last_seen_turn_id=1,
    )


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
