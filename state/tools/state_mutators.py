import os
import sqlite3
from typing import Any

from state.models.action import ActionEffect, ActionTemplate, EffectType

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")

class StateMutators:
    """受控写入器：封装所有状态变更逻辑，确保原子化和确定性"""

    def __init__(self, db_path: str = DB_PATH) -> None:
        """
        功能：初始化对象状态与依赖。
        入参：db_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        """
        功能：创建 SQLite 连接供受控写入流程复用。
        入参：无。
        出参：sqlite3.Connection，默认不自动提交，由调用方控制事务边界。
        异常：连接失败时抛出 sqlite3.Error，由上层决定回滚或失败降级。
        """
        return sqlite3.connect(self.db_path)

    def apply_action(self, action: ActionTemplate, use_shadow: bool = False) -> bool:
        """
        功能：应用一个完整的原子化动作。
        入参：action；use_shadow。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # 1. 验证前置条件 (简易演示)
        if not self._verify_preconditions(action.pre_conditions):
            return False

        # 2. 执行效果
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                for effect in action.success_effects:
                    self._execute_effect(cursor, effect, use_shadow)
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                print(f"Action execution failed: {e}")
                return False

    def _verify_preconditions(self, pre_conditions: list[dict[str, Any]]) -> bool:
        """
        功能：验证前置条件。
        入参：pre_conditions。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # TODO: 实现更复杂的逻辑运算符支持
        return True

    def _execute_effect(
        self,
        cursor: sqlite3.Cursor,
        effect: ActionEffect,
        use_shadow: bool,
    ) -> None:
        """
        功能：执行单个效果变更。
        入参：cursor（sqlite3.Cursor）：当前事务游标；effect（ActionEffect）：动作效果；
            use_shadow（bool）：为 True 时写 Shadow 表，默认 False 写 Active 表。
        出参：None。
        异常：SQL 执行失败抛出 sqlite3.Error；由 `apply_action` 统一捕获并回滚事务。
        """
        ent_table = "entities_shadow" if use_shadow else "entities_active"
        inv_table = "inventory_shadow" if use_shadow else "inventory_active"

        if effect.effect_type == EffectType.RESOURCE_CHANGE:
            attr = effect.parameters.get("attribute")
            value = effect.parameters.get("value", 0)
            # 安全更新 HP/MP
            cursor.execute(f"""
                UPDATE {ent_table}
                SET {attr} = MAX(0, MIN(max_{attr}, {attr} + ?))
                WHERE entity_id = ?
            """, (value, effect.target_id))

        elif effect.effect_type == EffectType.ITEM_TRANSFER:
            from_id = effect.parameters.get("from_id")
            to_id = effect.parameters.get("to_id")
            item_id = effect.parameters.get("item_id")
            qty = effect.parameters.get("quantity", 1)

            # 扣除来源
            cursor.execute(f"""
                UPDATE {inv_table} SET quantity = quantity - ?
                WHERE owner_id = ? AND item_id = ?
            """, (qty, from_id, item_id))
            # 增加去向
            cursor.execute(f"""
                INSERT INTO {inv_table} (owner_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id, item_id) DO UPDATE SET quantity = quantity + ?
            """, (to_id, item_id, qty, qty))

    def modify_hp(self, entity_id: str, amount: int, use_shadow: bool = False) -> bool:
        """
        功能：便捷方法：直接修改生命值。
        入参：entity_id；amount；use_shadow。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "entities_shadow" if use_shadow else "entities_active"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE {table}
                SET hp = MAX(0, MIN(max_hp, hp + ?))
                WHERE entity_id = ?
            """, (amount, entity_id))
            conn.commit()
            return int(cursor.rowcount) > 0
