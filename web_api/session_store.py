from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any, cast


class WebSessionStore:
    """
    功能：封装 Web 契约 API 的会话/回合/幂等持久化读写。
    入参：db_path（str）：SQLite 数据库绝对路径。
    出参：WebSessionStore，可供 service/blueprint 调用。
    异常：初始化不连接数据库；实际 SQL 异常在方法执行时向上抛出。
    """

    def __init__(self, db_path: str) -> None:
        """
        功能：保存数据库路径并初始化连接参数。
        入参：db_path（str）：SQLite 文件路径。
        出参：None。
        异常：无显式异常；参数非法导致后续连接失败时在调用阶段抛出。
        """
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """
        功能：创建 SQLite 连接并启用行工厂。
        入参：无。
        出参：sqlite3.Connection，带 `sqlite3.Row` 行访问能力。
        异常：数据库连接失败时抛出 sqlite3.Error。
        """
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def get_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        功能：查询幂等结果缓存。
        入参：scope（str）：幂等作用域。session_id（str）：会话标识。request_id（str）：请求标识。
        出参：dict[str, Any] | None，命中返回历史响应，不命中返回 None。
        异常：JSON 反序列化失败或 SQL 异常向上抛出。
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT response_json
                FROM web_idempotency_keys
                WHERE scope = ? AND session_id = ? AND request_id = ?
                """,
                (scope, session_id, request_id),
            ).fetchone()
        if row is None:
            return None
        # 幂等缓存约定保存 JSON 对象；若历史脏数据不是对象，则按未命中处理避免污染调用方契约。
        loaded = json.loads(str(row["response_json"]))
        if not isinstance(loaded, dict):
            return None
        return cast(dict[str, Any], loaded)

    def save_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> None:
        """
        功能：写入幂等结果缓存；重复键自动覆盖为同一响应。
        入参：scope（str）：作用域。session_id（str）：会话标识。
            request_id（str）：请求标识。response_payload（dict[str, Any]）：响应体。
        出参：None。
        异常：SQL 写入失败时抛出 sqlite3.Error。
        """
        response_json = json.dumps(response_payload, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(scope, session_id, request_id)
                DO UPDATE SET response_json = excluded.response_json
                """,
                (scope, session_id, request_id, response_json),
            )
            connection.commit()

    def _persist_turn_result_in_transaction(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        request_id: str,
        user_input: str,
        turn_result: dict[str, Any],
        memory_summary: str,
        now_iso: str,
    ) -> int:
        """
        功能：在调用方事务中持久化回合结果并推进会话游标。
        入参：connection（sqlite3.Connection）：已开启事务的连接；session_id（str）：会话标识；
            request_id（str）：请求标识；user_input（str）：玩家输入；
            turn_result（dict[str, Any]）：
            主循环回合结果；memory_summary（str）：摘要；now_iso（str）：更新时间。
        出参：int，持久化后的会话内回合号。
        异常：session 不存在、唯一约束冲突或 SQL 执行失败时抛出 sqlite3.Error；
            不在本函数捕获，交由上层事务决定回滚策略。
        """
        action_intent_json = (
            json.dumps(turn_result.get("action_intent"), ensure_ascii=False)
            if turn_result.get("action_intent") is not None
            else None
        )
        physics_diff_json = (
            json.dumps(turn_result.get("physics_diff"), ensure_ascii=False)
            if turn_result.get("physics_diff") is not None
            else None
        )
        session_row = connection.execute(
            """
            SELECT current_turn_id
            FROM web_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise sqlite3.IntegrityError("session_id 不存在，无法写入回合")
        current_turn_id = int(session_row["current_turn_id"])
        # Web 会话拥有独立回合序号；主循环返回的 turn_id 可能来自全局运行状态，
        # 不能直接写入会话历史，否则新会话会出现“第58回合”这类跳号摘要。
        persisted_turn_id = current_turn_id + 1
        connection.execute(
            """
            INSERT INTO web_session_turns(
                session_id, turn_id, request_id, user_input, is_valid,
                action_intent_json, physics_diff_json,
                final_response, memory_summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                persisted_turn_id,
                request_id,
                user_input,
                int(bool(turn_result.get("is_valid", False))),
                action_intent_json,
                physics_diff_json,
                str(turn_result.get("final_response", "")),
                memory_summary,
                now_iso,
            ),
        )
        connection.execute(
            """
            UPDATE web_sessions
            SET current_turn_id = ?, sandbox_mode = ?, memory_summary = ?,
                last_active_at = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                persisted_turn_id,
                int(bool(turn_result.get("is_sandbox_mode", False))),
                memory_summary,
                now_iso,
                now_iso,
                session_id,
            ),
        )
        return persisted_turn_id

    def persist_turn_result_with_idempotency(
        self,
        scope: str,
        session_id: str,
        request_id: str,
        user_input: str,
        turn_result: dict[str, Any],
        memory_summary: str,
        now_iso: str,
        response_builder: Callable[[int], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """
        功能：在单事务内完成幂等命中查询、回合落盘与幂等响应写入。
        入参：scope（str）：幂等作用域；session_id（str）：会话标识；request_id（str）：请求标识；
            user_input（str）：玩家输入；turn_result（dict[str, Any]）：主循环回合结果；
            memory_summary（str）：摘要；now_iso（str）：更新时间；
            response_builder（Callable[[int], dict[str, Any]]）：接收 session_turn_id
            并构造最终响应。
        出参：tuple[dict[str, Any], bool]，第一个值为响应 payload；
            第二个值表示是否新写入（True=新写入，False=命中幂等）。
        异常：response_builder 抛错、JSON 序列化失败或 SQL 异常时向上抛出；
            事务自动回滚，避免“已落盘未缓存”。
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT response_json
                FROM web_idempotency_keys
                WHERE scope = ? AND session_id = ? AND request_id = ?
                """,
                (scope, session_id, request_id),
            ).fetchone()
            if existing is not None:
                loaded = json.loads(str(existing["response_json"]))
                if isinstance(loaded, dict):
                    connection.commit()
                    return cast(dict[str, Any], loaded), False

            persisted_turn_id = self._persist_turn_result_in_transaction(
                connection=connection,
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
                turn_result=turn_result,
                memory_summary=memory_summary,
                now_iso=now_iso,
            )
            response_payload = response_builder(persisted_turn_id)
            response_json = json.dumps(response_payload, ensure_ascii=False)
            connection.execute(
                """
                INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(scope, session_id, request_id)
                DO UPDATE SET response_json = excluded.response_json
                """,
                (scope, session_id, request_id, response_json),
            )
            connection.commit()
            return response_payload, True

    def create_session(
        self,
        session_id: str,
        character_id: str,
        sandbox_mode: bool,
        now_iso: str,
        memory_policy: dict[str, Any],
    ) -> None:
        """
        功能：创建会话主记录。
        入参：session_id（str）：会话标识。character_id（str）：角色标识。
            sandbox_mode（bool）：沙盒开关。now_iso（str）：创建时间。
            memory_policy（dict[str, Any]）：记忆策略。
        出参：None。
        异常：会话主键冲突或 SQL 异常向上抛出。
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO web_sessions(
                    session_id,
                    character_id,
                    sandbox_mode,
                    current_turn_id,
                    memory_summary,
                    memory_policy_json,
                    created_at,
                    last_active_at,
                    updated_at
                )
                VALUES (?, ?, ?, 0, '', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    character_id,
                    int(sandbox_mode),
                    json.dumps(memory_policy, ensure_ascii=False),
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            connection.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """
        功能：读取单个会话信息。
        入参：session_id（str）：会话标识。
        出参：dict[str, Any] | None，存在返回结构化会话，不存在返回 None。
        异常：JSON 解析或 SQL 异常向上抛出。
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, character_id, sandbox_mode, current_turn_id,
                       memory_summary, memory_policy_json, created_at, last_active_at, updated_at
                FROM web_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        policy_raw = str(row["memory_policy_json"] or "")
        memory_policy = (
            json.loads(policy_raw)
            if policy_raw
            else {"mode": "auto", "max_turns": 20}
        )
        return {
            "session_id": str(row["session_id"]),
            "character_id": str(row["character_id"]),
            "sandbox_mode": bool(int(row["sandbox_mode"])),
            "current_turn_id": int(row["current_turn_id"]),
            "memory_summary": str(row["memory_summary"] or ""),
            "memory_policy": memory_policy,
            "created_at": str(row["created_at"] or ""),
            "last_active_at": str(row["last_active_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def update_memory_policy(
        self,
        session_id: str,
        memory_policy: dict[str, Any],
        now_iso: str,
    ) -> None:
        """
        功能：更新会话记忆策略。
        入参：session_id（str）：会话标识。memory_policy（dict[str, Any]）：新策略。
            now_iso（str）：更新时间。
        出参：None。
        异常：会话不存在时不抛异常（影响 0 行）；SQL 异常向上抛出。
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE web_sessions
                SET memory_policy_json = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (json.dumps(memory_policy, ensure_ascii=False), now_iso, session_id),
            )
            connection.commit()

    def update_memory_summary(
        self,
        session_id: str,
        memory_summary: str,
        now_iso: str,
    ) -> None:
        """
        功能：更新会话记忆摘要文本。
        入参：session_id（str）：会话标识。memory_summary（str）：摘要文本。
            now_iso（str）：更新时间。
        出参：None。
        异常：SQL 异常向上抛出。
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE web_sessions
                SET memory_summary = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (memory_summary, now_iso, session_id),
            )
            connection.commit()

    def list_turns(
        self,
        session_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """
        功能：分页读取会话回合摘要。
        入参：session_id（str）：会话标识。page（int）：页码。page_size（int）：分页大小。
        出参：tuple[int, list[dict[str, Any]]]，总条数与摘要列表。
        异常：SQL 异常向上抛出。
        """
        offset = (page - 1) * page_size
        with self._connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(1) AS total FROM web_session_turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT turn_id, is_valid, user_input, final_response, created_at
                FROM web_session_turns
                WHERE session_id = ?
                ORDER BY turn_id ASC
                LIMIT ? OFFSET ?
                """,
                (session_id, page_size, offset),
            ).fetchall()
        total = int(total_row["total"]) if total_row else 0
        items = [
            {
                "session_turn_id": int(row["turn_id"]),
                "is_valid": bool(int(row["is_valid"])),
                "user_input": str(row["user_input"]),
                "final_response": str(row["final_response"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
        return total, items

    def get_turn(self, session_id: str, session_turn_id: int) -> dict[str, Any] | None:
        """
        功能：读取指定回合详情。
        入参：session_id（str）：会话标识。session_turn_id（int）：会话内回合号。
        出参：dict[str, Any] | None，存在返回详情，不存在返回 None。
        异常：JSON 解析失败或 SQL 异常向上抛出。
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT turn_id, created_at, user_input, is_valid,
                       action_intent_json, physics_diff_json, final_response, memory_summary
                FROM web_session_turns
                WHERE session_id = ? AND turn_id = ?
                """,
                (session_id, session_turn_id),
            ).fetchone()
        if row is None:
            return None
        action_intent = (
            json.loads(str(row["action_intent_json"]))
            if row["action_intent_json"]
            else None
        )
        physics_diff = (
            json.loads(str(row["physics_diff_json"]))
            if row["physics_diff_json"]
            else None
        )
        return {
            "session_turn_id": int(row["turn_id"]),
            "created_at": str(row["created_at"]),
            "user_input": str(row["user_input"]),
            "is_valid": bool(int(row["is_valid"])),
            "action_intent": action_intent,
            "physics_diff": physics_diff,
            "final_response": str(row["final_response"]),
            "memory_summary": str(row["memory_summary"] or ""),
        }

    def get_recent_turns_for_memory(
        self,
        session_id: str,
        max_turns: int,
    ) -> list[dict[str, Any]]:
        """
        功能：按回合顺序读取最近 N 条回合，用于记忆摘要构建。
        入参：session_id（str）：会话标识。max_turns（int）：窗口大小。
        出参：list[dict[str, Any]]，从旧到新排序的回合列表。
        异常：SQL 异常向上抛出。
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_id, user_input, final_response
                FROM web_session_turns
                WHERE session_id = ?
                ORDER BY turn_id DESC
                LIMIT ?
                """,
                (session_id, max_turns),
            ).fetchall()
        return [
            {
                "turn_id": int(row["turn_id"]),
                "session_turn_id": int(row["turn_id"]),
                "user_input": str(row["user_input"]),
                "final_response": str(row["final_response"]),
            }
            for row in reversed(rows)
        ]

    def get_recent_story_turns_for_memory(
        self,
        session_id: str,
        max_turns: int,
    ) -> list[dict[str, Any]]:
        """
        功能：按回合顺序读取最近 N 条有效剧情回合，用于 story_memory 摘要构建。
        入参：session_id（str）：会话标识。max_turns（int）：窗口大小，需为正整数。
        出参：list[dict[str, Any]]，仅包含 is_valid=1 的回合，从旧到新排序。
        异常：SQL 异常向上抛出；JSON 字段不参与解析。
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_id, user_input, final_response
                FROM web_session_turns
                WHERE session_id = ? AND is_valid = 1
                ORDER BY turn_id DESC
                LIMIT ?
                """,
                (session_id, max_turns),
            ).fetchall()
        return [
            {
                "turn_id": int(row["turn_id"]),
                "session_turn_id": int(row["turn_id"]),
                "user_input": str(row["user_input"]),
                "final_response": str(row["final_response"]),
            }
            for row in reversed(rows)
        ]

    def persist_turn_result(
        self,
        session_id: str,
        request_id: str,
        user_input: str,
        turn_result: dict[str, Any],
        memory_summary: str,
        now_iso: str,
    ) -> int:
        """
        功能：持久化回合结果并更新会话游标与摘要。
        入参：session_id（str）：会话标识。request_id（str）：请求标识。
            user_input（str）：玩家输入。turn_result（dict[str, Any]）：回合结果。
            memory_summary（str）：摘要。now_iso（str）：更新时间。
        出参：int，实际持久化的会话内回合号，始终从当前会话游标顺延。
        异常：SQL 异常向上抛出；调用方需负责事务前后流程控制。
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            persisted_turn_id = self._persist_turn_result_in_transaction(
                connection=connection,
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
                turn_result=turn_result,
                memory_summary=memory_summary,
                now_iso=now_iso,
            )
            connection.commit()
            return persisted_turn_id

    def create_session_with_idempotency(
        self,
        *,
        scope: str,
        request_id: str,
        session_id: str,
        character_id: str,
        sandbox_mode: bool,
        now_iso: str,
        memory_policy: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """
        功能：在单事务内完成 create_session 幂等命中检查、会话创建与幂等响应写入。
        入参：scope/request_id（str）：幂等作用域与请求标识；
            session_id/character_id（str）：待创建会话及角色；
            sandbox_mode（bool）：沙盒开关；now_iso（str）：创建时间；
            memory_policy（dict[str, Any]）：会话记忆策略；
            response_payload（dict[str, Any]）：返回给客户端的幂等响应体。
        出参：tuple[dict[str, Any], bool]，第一个值为响应体；
            第二个值表示是否新创建（True=新创建，False=命中幂等）。
        异常：SQL/JSON 序列化异常向上抛出；事务自动回滚，避免会话与幂等键不一致。
        """
        response_json = json.dumps(response_payload, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT response_json
                FROM web_idempotency_keys
                WHERE scope = ? AND session_id = '' AND request_id = ?
                """,
                (scope, request_id),
            ).fetchone()
            if existing is not None:
                loaded = json.loads(str(existing["response_json"]))
                if isinstance(loaded, dict):
                    connection.commit()
                    return cast(dict[str, Any], loaded), False
            connection.execute(
                """
                INSERT INTO web_sessions(
                    session_id,
                    character_id,
                    sandbox_mode,
                    current_turn_id,
                    memory_summary,
                    memory_policy_json,
                    created_at,
                    last_active_at,
                    updated_at
                )
                VALUES (?, ?, ?, 0, '', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    character_id,
                    int(sandbox_mode),
                    json.dumps(memory_policy, ensure_ascii=False),
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            connection.execute(
                """
                INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
                VALUES(?, '', ?, ?)
                ON CONFLICT(scope, session_id, request_id)
                DO UPDATE SET response_json = excluded.response_json
                """,
                (scope, request_id, response_json),
            )
            connection.commit()
            return response_payload, True

    def clear_session_turns_and_reset(
        self,
        session_id: str,
        keep_character: bool,
        now_iso: str,
    ) -> bool:
        """
        功能：重置会话回合与记忆状态。
        入参：session_id（str）：会话标识。keep_character（bool）：是否保留角色绑定。
            now_iso（str）：更新时间。
        出参：bool，会话存在返回 True，不存在返回 False。
        异常：SQL 异常向上抛出。
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT character_id FROM web_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            character_id = str(row["character_id"]) if keep_character else "player_01"
            connection.execute(
                "DELETE FROM web_session_turns WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                """
                UPDATE web_sessions
                SET character_id = ?, sandbox_mode = 0, current_turn_id = 0,
                    memory_summary = '', last_active_at = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (character_id, now_iso, now_iso, session_id),
            )
            connection.commit()
            return True
