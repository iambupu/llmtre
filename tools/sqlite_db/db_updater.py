import json
import os
import sqlite3
from typing import cast

from state.tools.runtime_schema import ensure_runtime_tables

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")


class DBUpdater:
    """数据库状态流转管理器：处理 Active 与 Shadow 表之间的同步。"""

    def __init__(self, db_path: str = DB_PATH):
        """
        功能：初始化对象状态与依赖。
        入参：db_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path
        self._ensure_runtime_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """
        功能：执行 `_get_conn` 相关业务逻辑。
        入参：无。
        出参：sqlite3.Connection。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return sqlite3.connect(self.db_path)

    def begin_transaction(self) -> sqlite3.Connection:
        # 主循环写计划使用显式事务，保证同一回合原子提交。
        """
        功能：执行 `begin_transaction` 相关业务逻辑。
        入参：无。
        出参：sqlite3.Connection。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def commit_transaction(self, conn: sqlite3.Connection) -> None:
        """
        功能：执行 `commit_transaction` 相关业务逻辑。
        入参：conn。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        conn.commit()
        conn.close()

    def rollback_transaction(self, conn: sqlite3.Connection) -> None:
        """
        功能：执行 `rollback_transaction` 相关业务逻辑。
        入参：conn。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        conn.rollback()
        conn.close()

    def _ensure_runtime_tables(self) -> None:
        """
        功能：执行 `_ensure_runtime_tables` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            ensure_runtime_tables(cursor)
            conn.commit()

    def apply_diff(
        self,
        entity_id: str,
        diff: dict[str, object],
        use_shadow: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """
        功能：应用最小状态增量到实体表。
        入参：entity_id；diff；use_shadow；conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "entities_shadow" if use_shadow else "entities_active"
        # 当上层传入 conn 时复用同一事务；否则按单操作模式独立提交。
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            row = cursor.execute(
                f"""
                SELECT hp, max_hp, mp, max_mp, current_location_id, state_flags_json
                FROM {table}
                WHERE entity_id = ?
                """,
                (entity_id,),
            ).fetchone()
            if row is None:
                return False

            hp, max_hp, mp, max_mp, current_location_id, state_flags_json = row
            hp_delta_raw = diff.get("hp_delta", 0)
            mp_delta_raw = diff.get("mp_delta", 0)
            hp_delta = int(hp_delta_raw) if isinstance(hp_delta_raw, (int, float, str)) else 0
            mp_delta = int(mp_delta_raw) if isinstance(mp_delta_raw, (int, float, str)) else 0
            next_hp = max(0, min(int(max_hp), int(hp) + hp_delta))
            next_mp = max(0, min(int(max_mp), int(mp) + mp_delta))
            next_location_id = str(diff.get("location_id", current_location_id))

            flags: list[str] = []
            if state_flags_json:
                flags = list(json.loads(state_flags_json))
            state_flags_add = diff.get("state_flags_add", [])
            if isinstance(state_flags_add, list):
                for flag in state_flags_add:
                    if isinstance(flag, str) and flag not in flags:
                        flags.append(flag)

            cursor.execute(
                f"""
                UPDATE {table}
                SET hp = ?, mp = ?, current_location_id = ?,
                    state_flags_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE entity_id = ?
                """,
                (
                    next_hp,
                    next_mp,
                    next_location_id,
                    json.dumps(flags, ensure_ascii=False),
                    entity_id,
                ),
            )
            if owns_conn:
                active_conn.commit()
            return cursor.rowcount > 0
        finally:
            if owns_conn:
                active_conn.close()

    def advance_turn(self, turns: int = 1, conn: sqlite3.Connection | None = None) -> bool:
        """
        功能：推进时间轴回合数。
        入参：turns；conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            cursor.execute(
                "UPDATE timeline SET total_turns = total_turns + ? WHERE id = 0",
                (turns,),
            )
            if owns_conn:
                active_conn.commit()
            return cursor.rowcount > 0
        finally:
            if owns_conn:
                active_conn.close()

    def get_total_turns(self, conn: sqlite3.Connection | None = None) -> int:
        """
        功能：读取当前总回合数，供主循环生成真实 turn_id。
        入参：conn。
        出参：int。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            row = cursor.execute("SELECT total_turns FROM timeline WHERE id = 0").fetchone()
            if row is None:
                return 0
            return int(row[0] or 0)
        finally:
            if owns_conn:
                active_conn.close()

    def consume_item(
        self,
        owner_id: str,
        item_id: str,
        quantity: int = 1,
        use_shadow: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """
        功能：消费背包中的物品。
        入参：owner_id；item_id；quantity；use_shadow；conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "inventory_shadow" if use_shadow else "inventory_active"
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            row = cursor.execute(
                f"SELECT quantity FROM {table} WHERE owner_id = ? AND item_id = ?",
                (owner_id, item_id),
            ).fetchone()
            if row is None:
                return False

            remaining = int(row[0]) - quantity
            if remaining > 0:
                cursor.execute(
                    f"""
                    UPDATE {table}
                    SET quantity = ?
                    WHERE owner_id = ? AND item_id = ?
                    """,
                    (remaining, owner_id, item_id),
                )
            else:
                cursor.execute(
                    f"DELETE FROM {table} WHERE owner_id = ? AND item_id = ?",
                    (owner_id, item_id),
                )
            if owns_conn:
                active_conn.commit()
            return True
        finally:
            if owns_conn:
                active_conn.close()

    def has_shadow_state(self) -> bool:
        """
        功能：判断影子表中是否已有快照数据。
        入参：无。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT COUNT(1) FROM entities_shadow").fetchone()
            return bool(row and int(row[0]) > 0)

    def fork_shadow_state(self, conn: sqlite3.Connection | None = None) -> bool:
        """
        功能：Fork: 将 Active 表的数据克隆到 Shadow 表。
        入参：conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            try:
                cursor.execute("DELETE FROM entities_shadow")
                cursor.execute("DELETE FROM inventory_shadow")
                cursor.execute("DELETE FROM world_state_shadow")

                cursor.execute("INSERT INTO entities_shadow SELECT * FROM entities_active")
                cursor.execute("INSERT INTO inventory_shadow SELECT * FROM inventory_active")
                cursor.execute("INSERT INTO world_state_shadow SELECT * FROM world_state_active")

                if owns_conn:
                    active_conn.commit()
                return True
            except Exception:
                if owns_conn:
                    active_conn.rollback()
                return False
        finally:
            if owns_conn:
                active_conn.close()

    def merge_shadow_state(self, conn: sqlite3.Connection | None = None) -> bool:
        """
        功能：Merge: 将 Shadow 表的变更合并回 Active 表。
        入参：conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            try:
                cursor.execute("DELETE FROM entities_active")
                cursor.execute("INSERT INTO entities_active SELECT * FROM entities_shadow")

                cursor.execute("DELETE FROM inventory_active")
                cursor.execute("INSERT INTO inventory_active SELECT * FROM inventory_shadow")

                cursor.execute("DELETE FROM world_state_active")
                cursor.execute("INSERT INTO world_state_active SELECT * FROM world_state_shadow")

                if owns_conn:
                    active_conn.commit()
                return True
            except Exception:
                if owns_conn:
                    active_conn.rollback()
                return False
        finally:
            if owns_conn:
                active_conn.close()

    def drop_shadow_state(self, conn: sqlite3.Connection | None = None) -> bool:
        """
        功能：Drop: 丢弃影子表的所有变更。
        入参：conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        owns_conn = conn is None
        active_conn = conn or self._get_conn()
        try:
            cursor = active_conn.cursor()
            try:
                cursor.execute("DELETE FROM entities_shadow")
                cursor.execute("DELETE FROM inventory_shadow")
                cursor.execute("DELETE FROM world_state_shadow")
                if owns_conn:
                    active_conn.commit()
                return True
            except Exception:
                if owns_conn:
                    active_conn.rollback()
                return False
        finally:
            if owns_conn:
                active_conn.close()

    def is_achievement_unlocked(self, entity_id: str, achievement_id: str) -> bool:
        """
        功能：执行 `is_achievement_unlocked` 相关业务逻辑。
        入参：entity_id；achievement_id。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT 1 FROM achievement_unlocks
                WHERE entity_id = ? AND achievement_id = ?
                LIMIT 1
                """,
                (entity_id, achievement_id),
            ).fetchone()
            return row is not None

    def record_achievement_unlock(
        self,
        entity_id: str,
        achievement_id: str,
        description: str,
        reward: dict[str, object] | None = None,
    ) -> bool:
        """
        功能：执行 `record_achievement_unlock` 相关业务逻辑。
        入参：entity_id；achievement_id；description；reward。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        reward_json = json.dumps(reward or {}, ensure_ascii=False)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO achievement_unlocks
                (entity_id, achievement_id, description, reward_json)
                VALUES (?, ?, ?, ?)
                """,
                (entity_id, achievement_id, description, reward_json),
            )
            conn.commit()
            return cursor.rowcount > 0

    def enqueue_outer_event(self, event_name: str, payload: dict[str, object], error: str) -> int:
        """
        功能：执行 `enqueue_outer_event` 相关业务逻辑。
        入参：event_name；payload；error。
        出参：int。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO outer_event_outbox
                (event_name, payload_json, status, attempts, last_error, next_retry_at, updated_at)
                VALUES (?, ?, 'pending', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (event_name, payload_json, error),
            )
            conn.commit()
            lastrowid = cursor.lastrowid
            return int(lastrowid) if lastrowid is not None else 0

    def list_pending_outer_events(self, limit: int = 100) -> list[dict[str, object]]:
        """
        功能：按条件列举并返回集合数据。
        入参：limit。
        出参：list[dict[str, object]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT id, event_name, payload_json, attempts, last_error, status
                FROM outer_event_outbox
                WHERE status IN ('pending', 'retrying')
                  AND datetime(next_retry_at) <= datetime('now')
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result: list[dict[str, object]] = []
            for row in rows:
                result.append(
                    {
                        "id": int(row[0]),
                        "event_name": str(row[1]),
                        "payload": json.loads(row[2]),
                        "attempts": int(row[3] or 0),
                        "last_error": str(row[4] or ""),
                        "status": str(row[5] or "pending"),
                    }
                )
            return result

    def reserve_pending_outer_events(
        self,
        limit: int = 100,
        processing_timeout_seconds: int = 30,
    ) -> list[dict[str, object]]:
        """
        功能：执行 `reserve_pending_outer_events` 相关业务逻辑。
        入参：limit；processing_timeout_seconds。
        出参：list[dict[str, object]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.reclaim_stuck_processing_outer_events(timeout_seconds=processing_timeout_seconds)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                WITH picked AS (
                    SELECT id
                    FROM outer_event_outbox
                    WHERE status IN ('pending', 'retrying')
                      AND datetime(next_retry_at) <= datetime('now')
                    ORDER BY id ASC
                    LIMIT ?
                )
                UPDATE outer_event_outbox
                SET status = 'processing',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN (SELECT id FROM picked)
                  AND status IN ('pending', 'retrying')
                RETURNING id, event_name, payload_json, attempts, last_error, status
                """,
                (limit,),
            ).fetchall()
            conn.commit()
            result: list[dict[str, object]] = []
            for row in rows:
                result.append(
                    {
                        "id": int(row[0]),
                        "event_name": str(row[1]),
                        "payload": json.loads(row[2]),
                        "attempts": int(row[3] or 0),
                        "last_error": str(row[4] or ""),
                        "status": str(row[5] or "pending"),
                    }
            )
            return result

    def reclaim_stuck_processing_outer_events(self, timeout_seconds: int = 30) -> int:
        """
        功能：执行 `reclaim_stuck_processing_outer_events` 相关业务逻辑。
        入参：timeout_seconds。
        出参：int。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        timeout = max(1, int(timeout_seconds))
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE outer_event_outbox
                SET status = 'retrying',
                    next_retry_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                  AND datetime(updated_at) <= datetime('now', '-' || ? || ' seconds')
                """,
                (timeout,),
            )
            conn.commit()
            return int(cursor.rowcount)

    def mark_outer_event_delivered(self, event_id: int) -> bool:
        """
        功能：执行 `mark_outer_event_delivered` 相关业务逻辑。
        入参：event_id。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                    """
                UPDATE outer_event_outbox
                SET status = 'delivered', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (event_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_outer_event_failed(
        self,
        event_id: int,
        error: str,
        max_attempts: int = 5,
        base_backoff_seconds: int = 5,
    ) -> bool:
        """
        功能：执行 `mark_outer_event_failed` 相关业务逻辑。
        入参：event_id；error；max_attempts；base_backoff_seconds。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            cursor = conn.cursor()
            current = cursor.execute(
                "SELECT attempts FROM outer_event_outbox WHERE id = ?",
                (event_id,),
            ).fetchone()
            if current is None:
                return False
            attempts = int(current[0] or 0) + 1
            if attempts >= max_attempts:
                cursor.execute(
                    """
                    UPDATE outer_event_outbox
                    SET attempts = ?,
                        last_error = ?,
                        status = 'dead_letter',
                        dead_lettered_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (attempts, error, event_id),
                )
                conn.commit()
                return cursor.rowcount > 0

            backoff_seconds = max(1, int(base_backoff_seconds)) * (2 ** max(0, attempts - 1))
            cursor.execute(
                """
                UPDATE outer_event_outbox
                SET attempts = ?,
                    last_error = ?,
                    status = 'retrying',
                    next_retry_at = datetime('now', '+' || ? || ' seconds'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (attempts, error, backoff_seconds, event_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def upsert_world_state(
        self,
        key: str,
        value: dict[str, object],
        use_shadow: bool = False,
    ) -> bool:
        """
        功能：执行 `upsert_world_state` 相关业务逻辑。
        入参：key；value；use_shadow。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "world_state_shadow" if use_shadow else "world_state_active"
        payload = json.dumps(value, ensure_ascii=False)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO {table} (key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key)
                DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_world_state(self, key: str, use_shadow: bool = False) -> dict[str, object] | None:
        """
        功能：按条件读取并返回目标数据。
        入参：key；use_shadow。
        出参：dict[str, object] | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "world_state_shadow" if use_shadow else "world_state_active"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                f"SELECT value_json FROM {table} WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            loaded = json.loads(str(row[0] or "{}"))
            if isinstance(loaded, dict):
                return cast(dict[str, object], loaded)
            return None
