from __future__ import annotations

import sqlite3

from state.tools.db_initializer import deep_merge
from tools.sqlite_db.db_updater import DBUpdater


def _init_db_for_updater(db_path: str) -> None:
    """
    功能：初始化 DBUpdater 测试所需最小表结构与基线数据。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS entities_active (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER,
                max_hp INTEGER,
                mp INTEGER,
                max_mp INTEGER,
                current_location_id TEXT,
                state_flags_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS entities_shadow (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER,
                max_hp INTEGER,
                mp INTEGER,
                max_mp INTEGER,
                current_location_id TEXT,
                state_flags_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_active (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER,
                UNIQUE(owner_id, item_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_shadow (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER,
                UNIQUE(owner_id, item_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS world_state_active (
                key TEXT PRIMARY KEY,
                value_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS world_state_shadow (
                key TEXT PRIMARY KEY,
                value_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS timeline (
                id INTEGER PRIMARY KEY,
                current_time_minutes INTEGER NOT NULL DEFAULT 0,
                total_turns INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO timeline(id, current_time_minutes, total_turns)
            VALUES(0, 0, 0)
            """
        )
        conn.commit()


def test_deep_merge_respects_allowed_fields_and_deduplicates_list() -> None:
    """
    功能：验证 deep_merge 仅合并白名单字段，且列表追加会去重。
    入参：无。
    出参：None。
    异常：断言失败表示 MOD 合并策略回归。
    """
    base = {"a": 1, "meta": {"x": 1}, "tags": ["core"], "locked": "keep"}
    extension = {
        "a": 2,
        "meta": {"y": 2},
        "tags": ["core", "dlc"],
        "locked": "override",
    }
    merged = deep_merge(base, extension, allowed_fields=["a", "meta", "tags"])
    assert merged["a"] == 2
    assert merged["meta"] == {"x": 1, "y": 2}
    assert merged["tags"] == ["core", "dlc"]
    assert merged["locked"] == "keep"


def test_apply_diff_returns_false_when_entity_missing(tmp_path) -> None:
    """
    功能：验证实体不存在时 apply_diff 返回 False，避免误报成功。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示缺失实体分支退化。
    """
    db_path = str(tmp_path / "db_updater_missing.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)
    assert updater.apply_diff("missing", {"hp_delta": -1}) is False


def test_apply_diff_clamps_resources_and_merges_state_flags(tmp_path) -> None:
    """
    功能：验证 apply_diff 会进行 hp/mp 边界钳制，并去重合并 state_flags。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示状态增量约束回归。
    """
    db_path = str(tmp_path / "db_updater_apply.db")
    _init_db_for_updater(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entities_active(
                entity_id, hp, max_hp, mp, max_mp, current_location_id, state_flags_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("player_01", 5, 10, 1, 5, "loc_old", "[\"poisoned\"]"),
        )
        conn.commit()
    updater = DBUpdater(db_path)
    ok = updater.apply_diff(
        "player_01",
        {
            "hp_delta": "-99",
            "mp_delta": 99,
            "location_id": "loc_new",
            "state_flags_add": ["poisoned", "buffed", 42],
        },
    )
    assert ok is True
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT hp, mp, current_location_id, state_flags_json
            FROM entities_active
            WHERE entity_id = ?
            """,
            ("player_01",),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0
    assert int(row[1]) == 5
    assert str(row[2]) == "loc_new"
    assert str(row[3]) == "[\"poisoned\", \"buffed\"]"


def test_consume_item_covers_missing_update_and_delete_branch(tmp_path) -> None:
    """
    功能：验证 consume_item 在物品缺失/扣减后保留/扣减后删除三分支行为。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示库存消费分支退化。
    """
    db_path = str(tmp_path / "db_updater_consume.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)
    assert updater.consume_item("player_01", "potion", quantity=1) is False
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO inventory_active(owner_id, item_id, quantity) VALUES(?, ?, ?)",
            ("player_01", "potion", 2),
        )
        conn.commit()
    assert updater.consume_item("player_01", "potion", quantity=1) is True
    with sqlite3.connect(db_path) as conn:
        qty_row = conn.execute(
            "SELECT quantity FROM inventory_active WHERE owner_id = ? AND item_id = ?",
            ("player_01", "potion"),
        ).fetchone()
    assert qty_row is not None
    assert int(qty_row[0]) == 1
    assert updater.consume_item("player_01", "potion", quantity=1) is True
    with sqlite3.connect(db_path) as conn:
        deleted = conn.execute(
            "SELECT quantity FROM inventory_active WHERE owner_id = ? AND item_id = ?",
            ("player_01", "potion"),
        ).fetchone()
    assert deleted is None


def test_outer_event_failed_branches_retry_and_dead_letter(tmp_path) -> None:
    """
    功能：验证 outbox 失败分支可进入 retrying，超过阈值后进入 dead_letter。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示外环失败重试策略回归。
    """
    db_path = str(tmp_path / "db_updater_outbox.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)
    missing = updater.mark_outer_event_failed(999, "missing", max_attempts=2)
    assert missing is False
    event_id = updater.enqueue_outer_event("evt", {"k": "v"}, "init")
    assert event_id > 0
    first = updater.mark_outer_event_failed(
        event_id,
        "boom",
        max_attempts=4,
        base_backoff_seconds=2,
    )
    assert first is True
    with sqlite3.connect(db_path) as conn:
        row_retry = conn.execute(
            "SELECT attempts, status FROM outer_event_outbox WHERE id = ?",
            (event_id,),
        ).fetchone()
    assert row_retry is not None
    assert int(row_retry[0]) == 2
    assert str(row_retry[1]) == "retrying"
    second = updater.mark_outer_event_failed(
        event_id,
        "boom2",
        max_attempts=3,
        base_backoff_seconds=2,
    )
    assert second is True
    with sqlite3.connect(db_path) as conn:
        row_dead = conn.execute(
            "SELECT attempts, status FROM outer_event_outbox WHERE id = ?",
            (event_id,),
        ).fetchone()
    assert row_dead is not None
    assert int(row_dead[0]) == 3
    assert str(row_dead[1]) == "dead_letter"


def test_explicit_transaction_commit_and_rollback_control_visibility(tmp_path) -> None:
    """
    功能：验证显式事务 commit/rollback 会控制同一事务内 apply_diff/advance_turn 的持久化。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示显式事务边界回归。
    """
    db_path = str(tmp_path / "db_updater_tx.db")
    _init_db_for_updater(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entities_active(
                entity_id, hp, max_hp, mp, max_mp, current_location_id, state_flags_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("player_01", 5, 10, 2, 5, "loc_old", "[]"),
        )
        conn.commit()
    updater = DBUpdater(db_path)

    conn_commit = updater.begin_transaction()
    assert updater.apply_diff("player_01", {"hp_delta": 2}, conn=conn_commit) is True
    assert updater.advance_turn(3, conn=conn_commit) is True
    updater.commit_transaction(conn_commit)
    with sqlite3.connect(db_path) as check_conn:
        committed = check_conn.execute(
            "SELECT hp FROM entities_active WHERE entity_id = ?",
            ("player_01",),
        ).fetchone()
        turns = check_conn.execute("SELECT total_turns FROM timeline WHERE id = 0").fetchone()
    assert committed is not None
    assert int(committed[0]) == 7
    assert turns is not None
    assert int(turns[0]) == 3

    conn_rollback = updater.begin_transaction()
    assert updater.apply_diff("player_01", {"hp_delta": -5}, conn=conn_rollback) is True
    updater.rollback_transaction(conn_rollback)
    with sqlite3.connect(db_path) as check_conn:
        rolled_back = check_conn.execute(
            "SELECT hp FROM entities_active WHERE entity_id = ?",
            ("player_01",),
        ).fetchone()
    assert rolled_back is not None
    assert int(rolled_back[0]) == 7


def test_outer_outbox_reserve_reclaim_and_delivered_missing_branch(tmp_path) -> None:
    """
    功能：验证 outbox reserve 会标记 processing，stuck processing 可回收，
        缺失 delivered 返回 False。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 outbox 预留/回收/送达边界回归。
    """
    db_path = str(tmp_path / "db_updater_outbox_reserve.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)
    assert updater.mark_outer_event_delivered(999) is False
    first_id = updater.enqueue_outer_event("evt_one", {"n": 1}, "init")
    second_id = updater.enqueue_outer_event("evt_two", {"n": 2}, "init")

    reserved = updater.reserve_pending_outer_events(limit=1, processing_timeout_seconds=1)

    assert len(reserved) == 1
    assert reserved[0]["id"] == first_id
    assert reserved[0]["status"] == "processing"
    assert reserved[0]["payload"] == {"n": 1}
    with sqlite3.connect(db_path) as conn:
        statuses = dict(conn.execute("SELECT id, status FROM outer_event_outbox").fetchall())
    assert statuses[first_id] == "processing"
    assert statuses[second_id] == "pending"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE outer_event_outbox
            SET updated_at = datetime('now', '-10 seconds')
            WHERE id = ?
            """,
            (first_id,),
        )
        conn.commit()
    reclaimed = updater.reclaim_stuck_processing_outer_events(timeout_seconds=0)
    assert reclaimed == 1
    pending = updater.list_pending_outer_events(limit=10)
    assert {row["id"] for row in pending} == {first_id, second_id}
    assert updater.mark_outer_event_delivered(first_id) is True


def test_world_state_upsert_shadow_and_non_dict_read_returns_none(tmp_path) -> None:
    """
    功能：验证 world_state 可写入 Active/Shadow，读取非 dict JSON 时返回 None。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 world_state 读写或非 dict 降级回归。
    """
    db_path = str(tmp_path / "db_updater_world_state.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)

    assert updater.get_world_state("missing") is None
    assert updater.upsert_world_state("weather", {"state": "rain"}) is True
    assert updater.upsert_world_state("weather", {"state": "fog"}, use_shadow=True) is True
    assert updater.get_world_state("weather") == {"state": "rain"}
    assert updater.get_world_state("weather", use_shadow=True) == {"state": "fog"}

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO world_state_active(key, value_json) VALUES(?, ?)",
            ("bad", "[1, 2, 3]"),
        )
        conn.commit()
    assert updater.get_world_state("bad") is None


def test_shadow_state_lifecycle_fork_merge_and_drop(tmp_path) -> None:
    """
    功能：验证 Shadow 状态 fork/merge/drop 的完整生命周期。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 Active/Shadow 快照流转回归。
    """
    db_path = str(tmp_path / "db_updater_shadow.db")
    _init_db_for_updater(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entities_active(
                entity_id, hp, max_hp, mp, max_mp, current_location_id, state_flags_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("player_01", 10, 10, 5, 5, "active_loc", "[]"),
        )
        conn.execute(
            "INSERT INTO inventory_active(owner_id, item_id, quantity) VALUES(?, ?, ?)",
            ("player_01", "potion", 1),
        )
        conn.execute(
            "INSERT INTO world_state_active(key, value_json) VALUES(?, ?)",
            ("weather", "{\"state\":\"clear\"}"),
        )
        conn.commit()
    updater = DBUpdater(db_path)

    assert updater.has_shadow_state() is False
    assert updater.fork_shadow_state() is True
    assert updater.has_shadow_state() is True
    assert updater.apply_diff(
        "player_01",
        {"hp_delta": -3, "location_id": "shadow_loc"},
        use_shadow=True,
    )
    assert updater.upsert_world_state("weather", {"state": "rain"}, use_shadow=True)
    assert updater.merge_shadow_state() is True

    with sqlite3.connect(db_path) as conn:
        active = conn.execute(
            "SELECT hp, current_location_id FROM entities_active WHERE entity_id = ?",
            ("player_01",),
        ).fetchone()
        inventory = conn.execute(
            "SELECT quantity FROM inventory_active WHERE owner_id = ? AND item_id = ?",
            ("player_01", "potion"),
        ).fetchone()
    assert active is not None
    assert (int(active[0]), str(active[1])) == (7, "shadow_loc")
    assert inventory is not None
    assert int(inventory[0]) == 1

    assert updater.drop_shadow_state() is True
    assert updater.has_shadow_state() is False


def test_achievement_unlocks_are_inserted_once_and_queryable(tmp_path) -> None:
    """
    功能：验证成就解锁查询、首次记录与重复记录的幂等行为。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示成就记录契约回归。
    """
    db_path = str(tmp_path / "db_updater_achievements.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)

    assert updater.is_achievement_unlocked("player_01", "first_blood") is False
    assert updater.record_achievement_unlock(
        "player_01",
        "first_blood",
        "首次击败敌人",
        reward={"mp_delta": 1},
    ) is True
    assert updater.is_achievement_unlocked("player_01", "first_blood") is True
    assert updater.record_achievement_unlock(
        "player_01",
        "first_blood",
        "重复记录",
        reward=None,
    ) is False


def test_get_total_turns_returns_zero_when_timeline_missing(tmp_path) -> None:
    """
    功能：验证 timeline 基线行缺失时 get_total_turns 返回 0。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示总回合读取缺失行降级回归。
    """
    db_path = str(tmp_path / "db_updater_turns_missing.db")
    _init_db_for_updater(db_path)
    updater = DBUpdater(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM timeline WHERE id = 0")
        conn.commit()

    assert updater.get_total_turns() == 0
