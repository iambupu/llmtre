import json
import os
import sqlite3
from typing import Any, cast

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")

class EntityProbes:
    """只读状态探针：供 Agent 安全查询游戏现状"""

    def __init__(self, db_path: str = DB_PATH):
        """
        功能：初始化对象状态与依赖。
        入参：db_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """
        功能：执行 `_get_conn` 相关业务逻辑。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return sqlite3.connect(self.db_path)

    def get_character_stats(
        self,
        entity_id: str,
        use_shadow: bool = False,
    ) -> dict[str, Any] | None:
        """
        功能：获取角色的基础数值和资源。
        入参：entity_id；use_shadow。
        出参：dict[str, Any] | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "entities_shadow" if use_shadow else "entities_active"
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table} WHERE entity_id = ?", (entity_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def check_inventory(self, entity_id: str, use_shadow: bool = False) -> list[dict[str, Any]]:
        """
        功能：检查角色背包中的物品。
        入参：entity_id；use_shadow。
        出参：list[dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "inventory_shadow" if use_shadow else "inventory_active"
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT i.item_id, i.quantity, t.name, t.description, t.item_type
                FROM {table} i
                JOIN items t ON i.item_id = t.item_id
                WHERE i.owner_id = ?
            """, (entity_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_inventory_item(
        self,
        entity_id: str,
        item_id: str,
        use_shadow: bool = False,
    ) -> dict[str, Any] | None:
        """
        功能：获取背包中的某个具体物品。
        入参：entity_id；item_id；use_shadow。
        出参：dict[str, Any] | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        for row in self.check_inventory(entity_id, use_shadow=use_shadow):
            if row["item_id"] == item_id:
                return row
        return None

    def get_item_definition(self, item_id: str) -> dict[str, Any] | None:
        """
        功能：获取物品定义与效果。
        入参：item_id。
        出参：dict[str, Any] | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT item_id, name, description, item_type, effects_json, hooks_json, is_stackable
                FROM items
                WHERE item_id = ?
                """,
                (item_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item["effects"] = json.loads(item.pop("effects_json") or "[]")
            item["hooks"] = json.loads(item.pop("hooks_json") or "{}")
            return item

    def get_location_info(
        self,
        location_id: str,
        use_shadow: bool = False,
    ) -> dict[str, Any] | None:
        """
        功能：获取场景详细信息。
        入参：location_id；use_shadow。
        出参：dict[str, Any] | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "world_state_shadow" if use_shadow else "world_state_active"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT value_json FROM {table} WHERE key = ?", (f"loc_{location_id}",))
            row = cursor.fetchone()
            if row:
                loaded = json.loads(str(row[0]))
                if isinstance(loaded, dict):
                    return cast(dict[str, Any], loaded)
                return None
        return None

    def list_nearby_entities(
        self,
        location_id: str,
        use_shadow: bool = False,
    ) -> list[dict[str, Any]]:
        """
        功能：列出当前场景中的所有实体。
        入参：location_id；use_shadow。
        出参：list[dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "entities_shadow" if use_shadow else "entities_active"
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT entity_id, name, entity_type
                FROM {table}
                WHERE current_location_id = ?
                """,
                (location_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
