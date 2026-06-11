"""
功能：封装 Web 会话、回合与幂等缓存的 SQLite 持久化逻辑。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any, cast

from state.contracts.memory import NarrativeMemoryItem
from web_api.narrative_memory import (
    format_narrative_memory_context,
    rank_narrative_memory_items,
)

IDEMPOTENCY_STATUS_KEY = "__tre_idempotency_status"
IDEMPOTENCY_STATUS_PENDING = "pending"
IDEMPOTENCY_STATUS_ERROR = "error"
SESSION_RUNTIME_CHARACTER_PREFIX = "pc_"


def build_session_runtime_character_id(session_id: str) -> str:
    """
    功能：根据 Web session_id 生成会话专属运行角色 ID。
    入参：session_id（str）：已通过 SESSION_ID_PATTERN 校验的会话标识。
    出参：str，格式为 `pc_<session_id>`，用于 entities/inventory 的 session scoped owner。
    异常：不抛异常；调用方负责确保 session_id 长度满足角色 ID 契约。
    """
    return f"{SESSION_RUNTIME_CHARACTER_PREFIX}{session_id}"


def _json_object_or_empty(raw: Any) -> dict[str, Any]:
    """
    功能：将数据库或调用方传入的 JSON 对象字段解析为字典。
    入参：raw（Any）：可能为空、字符串化 JSON 或其他历史脏值。
    出参：dict[str, Any]，合法 JSON 对象返回浅拷贝，其他输入降级为空字典。
    异常：JSON 解析失败时内部捕获并降级为空字典，避免脏元数据阻断会话读写。
    """
    try:
        loaded = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _json_list_or_empty(raw: Any) -> list[Any]:
    """
    功能：标准化只接受内存态列表字段，过滤非列表输入。
    入参：raw（Any）：可能来自元数据合并阶段的任意值。
    出参：list[Any]，输入已经是 list 时原样返回，否则降级为空列表。
    异常：不抛异常；非法类型按空列表处理。
    """
    return raw if isinstance(raw, list) else []


def _unique_strings(raw: Any) -> list[str]:
    """
    功能：将列表字段标准化为去重后的非空字符串序列。
    入参：raw（Any）：期望为 list，但允许历史脏值或混合类型元素。
    出参：list[str]，保留首次出现顺序并移除空白、非字符串和重复值。
    异常：不抛异常；非列表输入和非法元素按空集合/跳过处理。
    """
    values = raw if isinstance(raw, list) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _dict_list(raw: Any) -> list[dict[str, Any]]:
    """
    功能：将列表字段过滤为字典列表，隔离历史或外部传入的非法元素。
    入参：raw（Any）：期望为 list[dict]，但可能包含非字典元素。
    出参：list[dict[str, Any]]，每个字典元素都会浅拷贝后返回。
    异常：不抛异常；非列表输入降级为空列表，非字典元素被跳过。
    """
    values = raw if isinstance(raw, list) else []
    return [dict(item) for item in values if isinstance(item, dict)]


def _extract_scene_progress_metadata(turn_result: dict[str, Any]) -> dict[str, str]:
    """
    功能：从回合结果中提取可用于会话选择器的当前场景进度摘要。
    入参：turn_result（dict[str, Any]）：标准化后的回合结果，
        可能包含 current_scene_id 或 scene_snapshot。
    出参：dict[str, str]，包含 current_scene_id/current_scene_title 中可确定的字段。
    异常：不抛异常；字段缺失或类型不符时返回空字典，避免坏回合结果覆盖已有进度。
    """
    progress: dict[str, str] = {}
    current_scene_id = str(turn_result.get("current_scene_id") or "").strip()
    current_scene_title = str(turn_result.get("current_scene_title") or "").strip()

    raw_scene_snapshot = turn_result.get("scene_snapshot")
    scene_snapshot = raw_scene_snapshot if isinstance(raw_scene_snapshot, dict) else {}
    raw_location = scene_snapshot.get("current_location")
    current_location = raw_location if isinstance(raw_location, dict) else {}
    if not current_scene_id:
        current_scene_id = str(
            current_location.get("id")
            or current_location.get("scene_id")
            or current_location.get("location_id")
            or ""
        ).strip()
    if not current_scene_title:
        current_scene_title = str(
            current_location.get("name") or current_location.get("label") or ""
        ).strip()

    # 摘要边界：这里只保存“玩家在哪里”的读取提示；结构化位置仍以角色状态表为准。
    if current_scene_id:
        progress["current_scene_id"] = current_scene_id
    if current_scene_title:
        progress["current_scene_title"] = current_scene_title
    return progress


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """
    功能：判断当前 SQLite 数据库是否存在指定表。
    入参：connection（sqlite3.Connection）：活动连接；table_name（str）：表名。
    出参：bool，存在返回 True。
    异常：SQL 查询失败时向上抛出，由调用方事务处理。
    """
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_idempotency_payload(raw: Any) -> dict[str, Any] | None:
    """
    功能：解析幂等缓存 JSON，并过滤非对象历史脏数据。
    入参：raw（Any）：数据库 response_json 原始值。
    出参：dict[str, Any] | None，合法 JSON 对象返回 dict，否则返回 None。
    异常：JSON 解析失败时内部降级为 None，避免脏缓存阻塞重新持久化。
    """
    try:
        loaded = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def _build_pending_idempotency_payload() -> dict[str, Any]:
    """
    功能：构造回合执行前的幂等占位记录。
    入参：无。
    出参：dict[str, Any]，包含内部状态标记，不作为业务成功响应返回。
    异常：不抛异常；纯内存常量组装。
    """
    return {IDEMPOTENCY_STATUS_KEY: IDEMPOTENCY_STATUS_PENDING}


def _is_pending_idempotency_payload(payload: dict[str, Any]) -> bool:
    """
    功能：判断幂等缓存是否为执行中占位。
    入参：payload（dict[str, Any]）：已解析幂等缓存对象。
    出参：bool，pending 标记命中返回 True。
    异常：不抛异常；缺字段按 False 处理。
    """
    return payload.get(IDEMPOTENCY_STATUS_KEY) == IDEMPOTENCY_STATUS_PENDING


def _is_error_idempotency_payload(payload: dict[str, Any]) -> bool:
    """
    功能：判断幂等缓存是否为失败响应占位。
    入参：payload（dict[str, Any]）：已解析幂等缓存对象。
    出参：bool，error 标记命中返回 True。
    异常：不抛异常；缺字段按 False 处理。
    """
    return payload.get(IDEMPOTENCY_STATUS_KEY) == IDEMPOTENCY_STATUS_ERROR


def _merge_session_metadata(
    existing: dict[str, Any],
    turn_result: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：把回合执行结果中的会话元数据合并回持久化 metadata。
    入参：existing（dict[str, Any]）：数据库中已有 metadata；
        turn_result（dict[str, Any]）：本回合主循环返回结果。
    出参：dict[str, Any]，包含合并后的元数据，并规范化触发器与任务状态列表。
    异常：不抛异常；缺失字段保留原值，非法列表元素在标准化阶段被丢弃。
    """
    metadata = dict(existing)
    result_metadata = turn_result.get("session_metadata")
    if isinstance(result_metadata, dict):
        metadata.update(result_metadata)

    if "fired_trigger_ids" in turn_result:
        metadata["fired_trigger_ids"] = _unique_strings(turn_result.get("fired_trigger_ids"))
    if "quest_states" in turn_result:
        metadata["quest_states"] = _dict_list(turn_result.get("quest_states"))
    elif "quest_updates" in turn_result and "quest_states" not in metadata:
        metadata["quest_states"] = _dict_list(turn_result.get("quest_updates"))
    if "fired_trigger_ids" in metadata:
        metadata["fired_trigger_ids"] = _unique_strings(metadata.get("fired_trigger_ids"))
    if "quest_states" in metadata:
        metadata["quest_states"] = _dict_list(metadata.get("quest_states"))
    metadata.update(_extract_scene_progress_metadata(turn_result))
    return metadata


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

    def _clone_runtime_character_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        base_character_id: str,
        runtime_character_id: str,
        now_iso: str,
        initial_location_id: str | None = None,
        reset_state_flags: bool = False,
        reset_resources: bool = False,
        initial_state_flags: list[str] | None = None,
    ) -> None:
        """
        功能：在调用方事务内克隆基准角色和背包为 session 专属运行角色。
        入参：connection（sqlite3.Connection）：已开启事务的连接；
            base_character_id（str）：基准角色 ID；runtime_character_id（str）：运行角色 ID；
            now_iso（str）：本次克隆更新时间；
            initial_location_id（str | None，默认 None）：会话绑定剧本起点，提供时覆盖克隆位置；
            reset_state_flags（bool，默认 False）：是否清空基准角色遗留状态标记；
            reset_resources（bool，默认 False）：是否把 HP/MP 恢复到 max_hp/max_mp；
            initial_state_flags（list[str] | None，默认 None）：剧本起点触发器产生的初始状态标签。
        出参：None。
        异常：实体表存在但基准角色缺失时抛 ValueError；SQL 失败向上抛出并由调用方回滚。
        """
        if base_character_id == runtime_character_id:
            return
        # 降级路径：部分轻量级 Web 单元测试只创建 runtime 表，不创建实体表。
        # 生产路径由 ensure_character_available 保证实体表和基准角色存在。
        if not _table_exists(connection, "entities_active"):
            return
        source = connection.execute(
            "SELECT * FROM entities_active WHERE entity_id = ?",
            (base_character_id,),
        ).fetchone()
        if source is None:
            raise ValueError(f"基准角色不存在，无法创建会话运行角色: {base_character_id}")
        columns = [
            str(item["name"]) for item in connection.execute("PRAGMA table_info(entities_active)")
        ]
        values: list[Any] = []
        normalized_initial_location_id = str(initial_location_id or "").strip()
        initial_flags_json = json.dumps(_unique_strings(initial_state_flags), ensure_ascii=False)
        max_hp_value = source["max_hp"] if "max_hp" in columns and source["max_hp"] else None
        max_mp_value = source["max_mp"] if "max_mp" in columns and source["max_mp"] else None
        for column in columns:
            if column == "entity_id":
                values.append(runtime_character_id)
            elif column == "updated_at":
                values.append(now_iso)
            elif column == "hp" and reset_resources and max_hp_value is not None:
                values.append(max_hp_value)
            elif column == "mp" and reset_resources and max_mp_value is not None:
                values.append(max_mp_value)
            elif column == "current_location_id" and normalized_initial_location_id:
                # 会话边界：pack 新会话应从 manifest.start_scene_id 开始，
                # 不继承基准角色上一次游玩留下的位置。
                values.append(normalized_initial_location_id)
            elif column == "state_flags_json" and reset_state_flags:
                # 会话边界：线索 flag 属于单个 session 进度，克隆时不继承基准角色；
                # 若起点场景触发器已产生初始 flag，则在同一事务内写入运行角色。
                values.append(initial_flags_json)
            else:
                values.append(source[column])
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        connection.execute(
            "DELETE FROM entities_active WHERE entity_id = ?",
            (runtime_character_id,),
        )
        connection.execute(
            f"INSERT INTO entities_active({column_sql}) VALUES({placeholders})",
            values,
        )
        if not _table_exists(connection, "inventory_active"):
            return
        connection.execute(
            "DELETE FROM inventory_active WHERE owner_id = ?",
            (runtime_character_id,),
        )
        connection.execute(
            """
            INSERT INTO inventory_active(owner_id, item_id, quantity)
            SELECT ?, item_id, quantity
            FROM inventory_active
            WHERE owner_id = ?
            """,
            (runtime_character_id, base_character_id),
        )

    def _grant_initial_items_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        runtime_character_id: str,
        initial_granted_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        功能：在会话创建事务内落地起点触发器授予的初始物品。
        入参：connection（sqlite3.Connection）：已开启事务的连接；
            runtime_character_id（str）：会话私有角色 ID；
            initial_granted_items（list[dict[str, Any]] | None，默认 None）：
            physics_diff.granted_items。
        出参：None。
        异常：SQL 异常向上抛出；缺表时按轻量测试环境降级跳过。
        """
        if not initial_granted_items or not _table_exists(connection, "inventory_active"):
            return
        for item in initial_granted_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "").strip()
            owner_id = str(item.get("owner_id") or runtime_character_id).strip()
            if not item_id or owner_id != runtime_character_id:
                continue
            try:
                quantity = max(1, int(item.get("quantity", 1)))
            except TypeError, ValueError:
                quantity = 1
            connection.execute(
                """
                INSERT INTO inventory_active(owner_id, item_id, quantity)
                VALUES(?, ?, ?)
                ON CONFLICT(owner_id, item_id)
                DO UPDATE SET quantity = quantity + excluded.quantity
                """,
                (runtime_character_id, item_id, quantity),
            )

    def _clear_session_shadow_state_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
    ) -> bool:
        """
        功能：在调用方事务内清理指定会话持有的全局 Shadow 沙盒状态。
        入参：connection（sqlite3.Connection）：已开启事务的连接；session_id（str）：会话 ID。
        出参：bool，当前会话持有沙盒并完成清理返回 True；未持有或锁表缺失返回 False。
        异常：SQL 异常向上抛出并由调用方回滚；表不存在时按兼容旧库跳过。
        """
        if not _table_exists(connection, "web_sandbox_lock"):
            return False
        row = connection.execute(
            "SELECT owner_session_id FROM web_sandbox_lock WHERE lock_id = 1",
        ).fetchone()
        if row is None or str(row["owner_session_id"] or "") != session_id:
            return False

        # Shadow 是全局单租约沙盒；owner 会话被删除时必须整体丢弃，避免孤儿沙盒阻塞后续会话。
        for table_name in ("entities_shadow", "inventory_shadow", "world_state_shadow"):
            if _table_exists(connection, table_name):
                connection.execute(f"DELETE FROM {table_name}")
        connection.execute("""
            UPDATE web_sandbox_lock
            SET owner_session_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE lock_id = 1
            """)
        return True

    def _delete_session_runtime_character_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        runtime_character_id: str,
    ) -> dict[str, int | bool]:
        """
        功能：在调用方事务内删除会话专属运行角色和背包数据。
        入参：connection（sqlite3.Connection）：已开启事务的连接；
            session_id（str）：会话 ID；runtime_character_id（str）：会话记录中的运行角色 ID。
        出参：dict[str, int | bool]，包含是否允许删除及 Active/Shadow 影响行数。
        异常：SQL 异常向上抛出；非 `pc_<session_id>` 运行角色会被拒绝删除以保护基础角色。
        """
        expected_runtime_character_id = build_session_runtime_character_id(session_id)
        if runtime_character_id != expected_runtime_character_id:
            return {
                "runtime_character_owned": False,
                "deleted_entities_active": 0,
                "deleted_inventory_active": 0,
                "deleted_entities_shadow": 0,
                "deleted_inventory_shadow": 0,
            }

        deleted_counts: dict[str, int | bool] = {
            "runtime_character_owned": True,
            "deleted_entities_active": 0,
            "deleted_inventory_active": 0,
            "deleted_entities_shadow": 0,
            "deleted_inventory_shadow": 0,
        }
        for table_name, column_name, result_key in (
            ("inventory_active", "owner_id", "deleted_inventory_active"),
            ("entities_active", "entity_id", "deleted_entities_active"),
            ("inventory_shadow", "owner_id", "deleted_inventory_shadow"),
            ("entities_shadow", "entity_id", "deleted_entities_shadow"),
        ):
            if not _table_exists(connection, table_name):
                continue
            cursor = connection.execute(
                f"DELETE FROM {table_name} WHERE {column_name} = ?",
                (runtime_character_id,),
            )
            deleted_counts[result_key] = int(cursor.rowcount if cursor.rowcount >= 0 else 0)
        return deleted_counts

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
        loaded = _load_idempotency_payload(row["response_json"])
        return loaded if loaded is not None else None

    def reserve_idempotent_request(
        self,
        scope: str,
        session_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        功能：在进入可能写世界状态的主循环前预留幂等键，或返回已缓存响应。
        入参：scope（str）：幂等作用域；session_id（str）：会话标识；request_id（str）：请求标识。
        出参：dict[str, Any] | None；已有缓存返回该缓存，新预留成功返回 None。
        异常：SQLite 写入失败时向上抛出；调用方需按 API/SSE 链路转换为受控错误。
        """
        pending_payload = _build_pending_idempotency_payload()
        pending_json = json.dumps(pending_payload, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT response_json
                FROM web_idempotency_keys
                WHERE scope = ? AND session_id = ? AND request_id = ?
                """,
                (scope, session_id, request_id),
            ).fetchone()
            if row is not None:
                loaded = _load_idempotency_payload(row["response_json"])
                if loaded is not None:
                    connection.commit()
                    return loaded
                # 降级路径：历史脏缓存不是 JSON 对象时，覆盖为 pending，让本次请求重新生成权威结果。
                connection.execute(
                    """
                    UPDATE web_idempotency_keys
                    SET response_json = ?
                    WHERE scope = ? AND session_id = ? AND request_id = ?
                    """,
                    (pending_json, scope, session_id, request_id),
                )
                connection.commit()
                return None
            connection.execute(
                """
                INSERT INTO web_idempotency_keys(scope, session_id, request_id, response_json)
                VALUES(?, ?, ?, ?)
                """,
                (scope, session_id, request_id, pending_json),
            )
            connection.commit()
            return None

    def save_idempotent_error_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
        response_body: dict[str, Any],
        status_code: int,
    ) -> None:
        """
        功能：缓存 post-run 失败响应，防止同一 request_id 重试时再次推进主循环。
        入参：scope/session_id/request_id（str）：幂等键；response_body（dict[str, Any]）：
            对外错误响应体；status_code（int）：HTTP 状态码。
        出参：None。
        异常：JSON 序列化或 SQLite 写入失败时向上抛出，由调用方记录但不覆盖原错误响应。
        """
        error_payload = {
            IDEMPOTENCY_STATUS_KEY: IDEMPOTENCY_STATUS_ERROR,
            "status_code": status_code,
            "body": response_body,
        }
        self.save_idempotent_response(
            scope=scope,
            session_id=session_id,
            request_id=request_id,
            response_payload=error_payload,
        )

    def clear_pending_idempotent_request(
        self,
        scope: str,
        session_id: str,
        request_id: str,
    ) -> None:
        """
        功能：清理仍处于 pending 的幂等预留，允许未写状态的失败请求后续重试。
        入参：scope/session_id/request_id（str）：幂等键。
        出参：None。
        异常：SQLite 删除失败时向上抛出；调用方记录后继续返回原业务错误。
        """
        pending_json = json.dumps(_build_pending_idempotency_payload(), ensure_ascii=False)
        with self._connect() as connection:
            # 事务边界：只删除本方法创建且尚未被成功/错误响应覆盖的 pending 占位。
            connection.execute(
                """
                DELETE FROM web_idempotency_keys
                WHERE scope = ? AND session_id = ? AND request_id = ? AND response_json = ?
                """,
                (scope, session_id, request_id, pending_json),
            )
            connection.commit()

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
        trigger_events_json = json.dumps(
            _json_list_or_empty(turn_result.get("trigger_events")),
            ensure_ascii=False,
        )
        quest_updates_json = json.dumps(
            _json_list_or_empty(turn_result.get("quest_updates")),
            ensure_ascii=False,
        )
        session_row = connection.execute(
            """
            SELECT current_turn_id, session_metadata_json
            FROM web_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise sqlite3.IntegrityError("session_id 不存在，无法写入回合")
        current_turn_id = int(session_row["current_turn_id"])
        existing_metadata = _json_object_or_empty(session_row["session_metadata_json"])
        session_metadata_json = json.dumps(
            _merge_session_metadata(existing_metadata, turn_result),
            ensure_ascii=False,
        )
        # Web 会话拥有独立回合序号；主循环返回的 turn_id 可能来自全局运行状态，
        # 不能直接写入会话历史，否则新会话会出现“第58回合”这类跳号摘要。
        persisted_turn_id = current_turn_id + 1
        connection.execute(
            """
            INSERT INTO web_session_turns(
                session_id, turn_id, request_id, user_input, is_valid,
                action_intent_json, physics_diff_json,
                trigger_events_json, quest_updates_json,
                final_response, memory_summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                persisted_turn_id,
                request_id,
                user_input,
                int(bool(turn_result.get("is_valid", False))),
                action_intent_json,
                physics_diff_json,
                trigger_events_json,
                quest_updates_json,
                str(turn_result.get("final_response", "")),
                memory_summary,
                now_iso,
            ),
        )
        connection.execute(
            """
            UPDATE web_sessions
            SET current_turn_id = ?, sandbox_mode = ?, memory_summary = ?,
                session_metadata_json = ?, last_active_at = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                persisted_turn_id,
                int(bool(turn_result.get("is_sandbox_mode", False))),
                memory_summary,
                session_metadata_json,
                now_iso,
                now_iso,
                session_id,
            ),
        )
        return persisted_turn_id

    def _upsert_narrative_memory_items_in_transaction(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        items: list[NarrativeMemoryItem],
        now_iso: str,
    ) -> None:
        """
        功能：在调用方事务中去重写入长期叙事记忆项。
        入参：connection（sqlite3.Connection）：已开启事务的连接；session_id（str）：会话标识；
            items（list[NarrativeMemoryItem]）：待写入记忆项；now_iso（str）：更新时间。
        出参：None。
        异常：SQL 执行失败或 JSON 序列化失败时向上抛出，调用方事务负责回滚。
        """
        for item in items:
            if item.session_id != session_id:
                continue
            metadata_json = json.dumps(item.metadata, ensure_ascii=False)
            connection.execute(
                """
                INSERT INTO web_session_memory_items(
                    memory_id, session_id, scope, kind, subject_type, subject_id, text,
                    evidence_turn_id, importance, confidence, status, metadata_json,
                    created_turn_id, last_seen_turn_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id)
                DO UPDATE SET
                    evidence_turn_id = excluded.evidence_turn_id,
                    importance = max(web_session_memory_items.importance, excluded.importance),
                    confidence = max(web_session_memory_items.confidence, excluded.confidence),
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    last_seen_turn_id = max(
                        web_session_memory_items.last_seen_turn_id,
                        excluded.last_seen_turn_id
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    item.memory_id,
                    item.session_id,
                    item.scope,
                    item.kind,
                    item.subject_type,
                    item.subject_id,
                    item.text,
                    item.evidence_turn_id,
                    item.importance,
                    item.confidence,
                    item.status,
                    metadata_json,
                    item.created_turn_id,
                    item.last_seen_turn_id,
                    now_iso,
                    now_iso,
                ),
            )

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
        memory_items_builder: Callable[[int], list[NarrativeMemoryItem]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """
        功能：在单事务内完成幂等命中查询、回合落盘与幂等响应写入。
        入参：scope（str）：幂等作用域；session_id（str）：会话标识；request_id（str）：请求标识；
            user_input（str）：玩家输入；turn_result（dict[str, Any]）：主循环回合结果；
            memory_summary（str）：摘要；now_iso（str）：更新时间；
            response_builder（Callable[[int], dict[str, Any]]）：接收 session_turn_id
            并构造最终响应；memory_items_builder（Callable | None，默认 None）：
            接收实际 session_turn_id 并返回长期叙事记忆项。
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
                loaded = _load_idempotency_payload(existing["response_json"])
                if loaded is not None and not (
                    _is_pending_idempotency_payload(loaded) or _is_error_idempotency_payload(loaded)
                ):
                    connection.commit()
                    return loaded, False

            persisted_turn_id = self._persist_turn_result_in_transaction(
                connection=connection,
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
                turn_result=turn_result,
                memory_summary=memory_summary,
                now_iso=now_iso,
            )
            if memory_items_builder is not None:
                memory_items = memory_items_builder(persisted_turn_id)
                self._upsert_narrative_memory_items_in_transaction(
                    connection=connection,
                    session_id=session_id,
                    items=memory_items,
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
        pack_metadata: dict[str, Any] | None = None,
        persona_profile: dict[str, Any] | None = None,
        runtime_character_id: str | None = None,
        initial_location_id: str | None = None,
        initial_session_metadata: dict[str, Any] | None = None,
        initial_state_flags: list[str] | None = None,
        initial_granted_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        功能：创建会话主记录。
        入参：session_id（str）：会话标识。character_id（str）：角色标识。
            sandbox_mode（bool）：沙盒开关。now_iso（str）：创建时间。
            memory_policy（dict[str, Any]）：记忆策略。
            pack_metadata（dict[str, Any] | None，默认 None）：A2 pack 绑定摘要。
            persona_profile（dict[str, Any] | None，默认 None）：玩家 persona 草案。
            runtime_character_id（str | None，默认 None）：会话专属运行角色 ID；
            空值表示沿用基准角色。
            initial_location_id（str | None，默认 None）：剧本起点场景，提供时写入运行角色。
            initial_session_metadata（dict[str, Any] | None，默认 None）：
            起点触发器冻结的会话元数据；
            initial_state_flags（list[str] | None，默认 None）：起点触发器写入的运行角色标签；
            initial_granted_items（list[dict[str, Any]] | None，默认 None）：起点触发器授予的物品。
        出参：None。
        异常：会话主键冲突或 SQL 异常向上抛出。
        """
        pack_metadata = pack_metadata or {}
        persona_profile = persona_profile or {}
        runtime_character_id = runtime_character_id or character_id
        initial_session_metadata = initial_session_metadata or {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._clone_runtime_character_in_transaction(
                connection,
                base_character_id=character_id,
                runtime_character_id=runtime_character_id,
                now_iso=now_iso,
                initial_location_id=initial_location_id,
                reset_state_flags=bool(initial_location_id),
                reset_resources=bool(initial_location_id),
                initial_state_flags=initial_state_flags,
            )
            self._grant_initial_items_in_transaction(
                connection,
                runtime_character_id=runtime_character_id,
                initial_granted_items=initial_granted_items,
            )
            connection.execute(
                """
                INSERT INTO web_sessions(
                    session_id,
                    character_id,
                    base_character_id,
                    runtime_character_id,
                    sandbox_mode,
                    current_turn_id,
                    memory_summary,
                    memory_policy_json,
                    pack_id,
                    scenario_id,
                    pack_version,
                    compiled_artifact_hash,
                    persona_profile_json,
                    session_metadata_json,
                    created_at,
                    last_active_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    character_id,
                    character_id,
                    runtime_character_id,
                    int(sandbox_mode),
                    json.dumps(memory_policy, ensure_ascii=False),
                    pack_metadata.get("pack_id"),
                    pack_metadata.get("scenario_id"),
                    pack_metadata.get("pack_version"),
                    pack_metadata.get("compiled_artifact_hash"),
                    json.dumps(persona_profile, ensure_ascii=False),
                    json.dumps(initial_session_metadata, ensure_ascii=False),
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
                SELECT session_id, character_id, base_character_id, runtime_character_id,
                       sandbox_mode, current_turn_id,
                       memory_summary, memory_policy_json,
                       pack_id, scenario_id, pack_version, compiled_artifact_hash,
                       persona_profile_json, session_metadata_json,
                       created_at, last_active_at, updated_at
                FROM web_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        policy_raw = str(row["memory_policy_json"] or "")
        memory_policy = json.loads(policy_raw) if policy_raw else {"mode": "auto", "max_turns": 20}
        persona_raw = str(row["persona_profile_json"] or "{}")
        persona_profile = json.loads(persona_raw) if persona_raw else {}
        if not isinstance(persona_profile, dict):
            persona_profile = {}
        session_metadata = _json_object_or_empty(row["session_metadata_json"])
        return {
            "session_id": str(row["session_id"]),
            "character_id": str(row["character_id"]),
            "base_character_id": str(row["base_character_id"] or row["character_id"]),
            "runtime_character_id": str(row["runtime_character_id"] or row["character_id"]),
            "sandbox_mode": bool(int(row["sandbox_mode"])),
            "current_turn_id": int(row["current_turn_id"]),
            "memory_summary": str(row["memory_summary"] or ""),
            "memory_policy": memory_policy,
            "pack_id": row["pack_id"],
            "scenario_id": row["scenario_id"],
            "pack_version": row["pack_version"],
            "compiled_artifact_hash": row["compiled_artifact_hash"],
            "persona_profile": persona_profile,
            "session_metadata": session_metadata,
            "created_at": str(row["created_at"] or ""),
            "last_active_at": str(row["last_active_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """
        功能：按最近活动时间读取 Web 会话摘要，供前端会话选择器展示真实保存进度。
        入参：limit（int，默认 20）：最多返回数量；调用方负责限制合理范围。
        出参：list[dict[str, Any]]，每项包含会话 ID、角色、pack 绑定、回合数和当前场景 ID。
        异常：JSON 解析或 SQL 异常向上抛出；单条脏 metadata 由 _json_object_or_empty 降级为空。
        """
        query_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, character_id, base_character_id, runtime_character_id,
                       sandbox_mode, current_turn_id, memory_summary,
                       pack_id, scenario_id, pack_version, compiled_artifact_hash,
                       session_metadata_json, created_at, last_active_at, updated_at
                FROM web_sessions
                ORDER BY last_active_at DESC, created_at DESC
                LIMIT ?
                """,
                (query_limit,),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            metadata = _json_object_or_empty(row["session_metadata_json"])
            current_scene_id = str(metadata.get("current_scene_id") or "").strip() or None
            current_scene_title = str(metadata.get("current_scene_title") or "").strip() or None
            summaries.append(
                {
                    "session_id": str(row["session_id"]),
                    "character_id": str(row["character_id"]),
                    "base_character_id": str(row["base_character_id"] or row["character_id"]),
                    "runtime_character_id": str(row["runtime_character_id"] or row["character_id"]),
                    "sandbox_mode": bool(int(row["sandbox_mode"])),
                    "current_session_turn_id": int(row["current_turn_id"]),
                    "memory_summary": str(row["memory_summary"] or ""),
                    "pack_id": row["pack_id"],
                    "scenario_id": row["scenario_id"],
                    "pack_version": row["pack_version"],
                    "compiled_artifact_hash": row["compiled_artifact_hash"],
                    "current_scene_id": current_scene_id,
                    "current_scene_title": current_scene_title,
                    "created_at": str(row["created_at"] or ""),
                    "last_active_at": str(row["last_active_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
        return summaries

    def delete_session(self, session_id: str) -> dict[str, Any] | None:
        """
        功能：删除一个 Web 会话及其私有运行数据。
        入参：session_id（str）：已通过路由校验的会话 ID。
        出参：dict[str, Any] | None，存在时返回删除摘要；会话不存在返回 None。
        异常：SQL 异常向上抛出；事务失败时所有删除会整体回滚。
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT session_id, character_id, base_character_id, runtime_character_id
                FROM web_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None

            runtime_character_id = str(
                row["runtime_character_id"] or build_session_runtime_character_id(session_id)
            )
            shadow_state_cleared = self._clear_session_shadow_state_in_transaction(
                connection,
                session_id=session_id,
            )
            runtime_delete_counts = self._delete_session_runtime_character_in_transaction(
                connection,
                session_id=session_id,
                runtime_character_id=runtime_character_id,
            )

            # 删除顺序先从子表和幂等缓存开始，最后移除 web_sessions 主记录。
            # 这样避免部分读路径看到孤儿子表。
            deleted_turns = connection.execute(
                "DELETE FROM web_session_turns WHERE session_id = ?",
                (session_id,),
            ).rowcount
            deleted_memory_items = connection.execute(
                "DELETE FROM web_session_memory_items WHERE session_id = ?",
                (session_id,),
            ).rowcount
            deleted_idempotency_keys = connection.execute(
                "DELETE FROM web_idempotency_keys WHERE session_id = ?",
                (session_id,),
            ).rowcount
            deleted_sessions = connection.execute(
                "DELETE FROM web_sessions WHERE session_id = ?",
                (session_id,),
            ).rowcount
            connection.commit()
            return {
                "deleted_session_id": session_id,
                "character_id": str(row["character_id"]),
                "base_character_id": str(row["base_character_id"] or row["character_id"]),
                "runtime_character_id": runtime_character_id,
                "deleted_sessions": int(deleted_sessions if deleted_sessions >= 0 else 0),
                "deleted_turns": int(deleted_turns if deleted_turns >= 0 else 0),
                "deleted_memory_items": int(
                    deleted_memory_items if deleted_memory_items >= 0 else 0
                ),
                "deleted_idempotency_keys": int(
                    deleted_idempotency_keys if deleted_idempotency_keys >= 0 else 0
                ),
                "deleted_shadow_state": shadow_state_cleared,
                **runtime_delete_counts,
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
                       action_intent_json, physics_diff_json,
                       trigger_events_json, quest_updates_json,
                       final_response, memory_summary
                FROM web_session_turns
                WHERE session_id = ? AND turn_id = ?
                """,
                (session_id, session_turn_id),
            ).fetchone()
        if row is None:
            return None
        action_intent = (
            json.loads(str(row["action_intent_json"])) if row["action_intent_json"] else None
        )
        physics_diff = (
            json.loads(str(row["physics_diff_json"])) if row["physics_diff_json"] else None
        )
        trigger_events = (
            json.loads(str(row["trigger_events_json"])) if row["trigger_events_json"] else []
        )
        quest_updates = (
            json.loads(str(row["quest_updates_json"])) if row["quest_updates_json"] else []
        )
        return {
            "session_turn_id": int(row["turn_id"]),
            "created_at": str(row["created_at"]),
            "user_input": str(row["user_input"]),
            "is_valid": bool(int(row["is_valid"])),
            "action_intent": action_intent,
            "physics_diff": physics_diff,
            "trigger_events": trigger_events if isinstance(trigger_events, list) else [],
            "quest_updates": quest_updates if isinstance(quest_updates, list) else [],
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

    def list_narrative_memory_items(
        self,
        session_id: str,
        limit: int = 8,
        relevance: dict[str, set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        功能：读取会话级 active 长期叙事记忆项，按 GM 消费优先级排序。
        入参：session_id（str）：会话标识；limit（int，默认 8）：返回上限，需为正整数；
            relevance（dict[str, set[str]] | None，默认 None）：当前场景地点/NPC/任务相关键。
        出参：list[dict[str, Any]]，包含格式化上下文所需字段。
        异常：SQL 或 JSON 解析异常向上抛出；调用方可按空长期记忆降级。
        """
        safe_limit = max(1, int(limit))
        # 检索边界：有相关性过滤时先取更多候选，再在 Python 侧按场景对象裁剪。
        # 这样避免动态 SQL 组合过多，也不会把无关 NPC/地点直接注入 GM。
        query_limit = max(safe_limit, safe_limit * 4) if relevance else safe_limit
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, session_id, scope, kind, subject_type, subject_id, text,
                       evidence_turn_id, importance, confidence, status, metadata_json,
                       created_turn_id, last_seen_turn_id, created_at, updated_at
                FROM web_session_memory_items
                WHERE session_id = ? AND status = 'active'
                ORDER BY importance DESC, last_seen_turn_id DESC, created_turn_id DESC
                LIMIT ?
                """,
                (session_id, query_limit),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            metadata = _json_object_or_empty(row["metadata_json"])
            items.append(
                {
                    "memory_id": str(row["memory_id"]),
                    "session_id": str(row["session_id"]),
                    "scope": str(row["scope"]),
                    "kind": str(row["kind"]),
                    "subject_type": str(row["subject_type"]),
                    "subject_id": str(row["subject_id"]),
                    "text": str(row["text"]),
                    "evidence_turn_id": int(row["evidence_turn_id"]),
                    "importance": int(row["importance"]),
                    "confidence": float(row["confidence"]),
                    "status": str(row["status"]),
                    "metadata": metadata,
                    "created_turn_id": int(row["created_turn_id"]),
                    "last_seen_turn_id": int(row["last_seen_turn_id"]),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
        if relevance:
            return rank_narrative_memory_items(items, relevance)[:safe_limit]
        return items[:safe_limit]

    def build_narrative_memory_context(
        self,
        session_id: str,
        limit: int = 8,
        relevance: dict[str, set[str]] | None = None,
    ) -> str:
        """
        功能：构建 GM 可直接消费的会话长期叙事记忆上下文。
        入参：session_id（str）：会话标识；limit（int，默认 8）：最多纳入条目数；
            relevance（dict[str, set[str]] | None，默认 None）：当前场景相关键。
        出参：str，空记忆返回空字符串。
        异常：SQL 或 JSON 解析异常向上抛出；上层主循环入口负责降级。
        """
        items = self.list_narrative_memory_items(
            session_id=session_id,
            limit=limit,
            relevance=relevance,
        )
        return format_narrative_memory_context(items, max_items=limit)

    def persist_turn_result(
        self,
        session_id: str,
        request_id: str,
        user_input: str,
        turn_result: dict[str, Any],
        memory_summary: str,
        now_iso: str,
        memory_items_builder: Callable[[int], list[NarrativeMemoryItem]] | None = None,
    ) -> int:
        """
        功能：持久化回合结果并更新会话游标与摘要。
        入参：session_id（str）：会话标识。request_id（str）：请求标识。
            user_input（str）：玩家输入。turn_result（dict[str, Any]）：回合结果。
            memory_summary（str）：摘要。now_iso（str）：更新时间。
            memory_items_builder（Callable | None，默认 None）：接收实际回合号并生成长期记忆项。
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
            if memory_items_builder is not None:
                memory_items = memory_items_builder(persisted_turn_id)
                self._upsert_narrative_memory_items_in_transaction(
                    connection=connection,
                    session_id=session_id,
                    items=memory_items,
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
        pack_metadata: dict[str, Any] | None = None,
        persona_profile: dict[str, Any] | None = None,
        runtime_character_id: str | None = None,
        initial_location_id: str | None = None,
        initial_session_metadata: dict[str, Any] | None = None,
        initial_state_flags: list[str] | None = None,
        initial_granted_items: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """
        功能：在单事务内完成 create_session 幂等命中检查、会话创建与幂等响应写入。
        入参：scope/request_id（str）：幂等作用域与请求标识；
            session_id/character_id（str）：待创建会话及角色；
            sandbox_mode（bool）：沙盒开关；now_iso（str）：创建时间；
            memory_policy（dict[str, Any]）：会话记忆策略；
            response_payload（dict[str, Any]）：返回给客户端的幂等响应体。
            pack_metadata（dict[str, Any] | None，默认 None）：A2 pack 绑定摘要；
            persona_profile（dict[str, Any] | None，默认 None）：玩家 persona 草案。
            runtime_character_id（str | None，默认 None）：会话专属运行角色 ID；
            空值表示沿用基准角色。
            initial_location_id（str | None，默认 None）：剧本起点场景，提供时写入运行角色。
            initial_session_metadata（dict[str, Any] | None，默认 None）：
            起点触发器冻结的会话元数据；
            initial_state_flags（list[str] | None，默认 None）：起点触发器写入的运行角色标签；
            initial_granted_items（list[dict[str, Any]] | None，默认 None）：起点触发器授予的物品。
        出参：tuple[dict[str, Any], bool]，第一个值为响应体；
            第二个值表示是否新创建（True=新创建，False=命中幂等）。
        异常：SQL/JSON 序列化异常向上抛出；事务自动回滚，避免会话与幂等键不一致。
        """
        pack_metadata = pack_metadata or {}
        persona_profile = persona_profile or {}
        runtime_character_id = runtime_character_id or character_id
        initial_session_metadata = initial_session_metadata or {}
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
            self._clone_runtime_character_in_transaction(
                connection,
                base_character_id=character_id,
                runtime_character_id=runtime_character_id,
                now_iso=now_iso,
                initial_location_id=initial_location_id,
                reset_state_flags=bool(initial_location_id),
                reset_resources=bool(initial_location_id),
                initial_state_flags=initial_state_flags,
            )
            self._grant_initial_items_in_transaction(
                connection,
                runtime_character_id=runtime_character_id,
                initial_granted_items=initial_granted_items,
            )
            connection.execute(
                """
                INSERT INTO web_sessions(
                    session_id,
                    character_id,
                    base_character_id,
                    runtime_character_id,
                    sandbox_mode,
                    current_turn_id,
                    memory_summary,
                    memory_policy_json,
                    pack_id,
                    scenario_id,
                    pack_version,
                    compiled_artifact_hash,
                    persona_profile_json,
                    session_metadata_json,
                    created_at,
                    last_active_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    character_id,
                    character_id,
                    runtime_character_id,
                    int(sandbox_mode),
                    json.dumps(memory_policy, ensure_ascii=False),
                    pack_metadata.get("pack_id"),
                    pack_metadata.get("scenario_id"),
                    pack_metadata.get("pack_version"),
                    pack_metadata.get("compiled_artifact_hash"),
                    json.dumps(persona_profile, ensure_ascii=False),
                    json.dumps(initial_session_metadata, ensure_ascii=False),
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
        initial_location_id: str | None = None,
    ) -> bool:
        """
        功能：重置会话回合与记忆状态。
        入参：session_id（str）：会话标识。keep_character（bool）：是否保留角色绑定。
            now_iso（str）：更新时间。
            initial_location_id（str | None，默认 None）：剧本起点场景，提供时写入运行角色。
        出参：bool，会话存在返回 True，不存在返回 False。
        异常：SQL 异常向上抛出。
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT character_id, base_character_id, runtime_character_id
                FROM web_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            character_id = str(row["base_character_id"] or row["character_id"])
            if not keep_character:
                character_id = "player_01"
            runtime_character_id = str(
                row["runtime_character_id"] or build_session_runtime_character_id(session_id)
            )
            self._clone_runtime_character_in_transaction(
                connection,
                base_character_id=character_id,
                runtime_character_id=runtime_character_id,
                now_iso=now_iso,
                initial_location_id=initial_location_id,
                reset_state_flags=bool(initial_location_id),
                reset_resources=bool(initial_location_id),
            )
            connection.execute(
                "DELETE FROM web_session_turns WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM web_session_memory_items WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM web_idempotency_keys WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                """
                UPDATE web_sessions
                SET character_id = ?, base_character_id = ?, runtime_character_id = ?,
                    sandbox_mode = 0, current_turn_id = 0,
                    memory_summary = '', session_metadata_json = '{}',
                    last_active_at = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (character_id, character_id, runtime_character_id, now_iso, now_iso, session_id),
            )
            connection.commit()
            return True
