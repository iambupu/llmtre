"""
后台异步演化智能体逻辑
"""

from __future__ import annotations

import hashlib

from config.agent_model_loader import get_agent_model_binding
from tools.sqlite_db.db_updater import DBUpdater


class EvolutionAgent:
    """世界演化智能体"""

    def __init__(
        self,
        db_updater: DBUpdater | None = None,
        model_binding_key: str = "agents.evolution",
    ):
        """
        功能：初始化演化智能体，并读取其模型绑定配置；本阶段只保留绑定信息，不启用真实模型。
        入参：db_updater（DBUpdater | None）：数据库更新器；为空时按默认路径构造。
        入参：model_binding_key（str）：Agent 模型绑定键，默认值为 `agents.evolution`。
        出参：无显式返回值；实例初始化后会暴露 `model_binding` 只读配置快照。
        异常：数据库更新器初始化异常默认向上抛出；模型配置缺失时内部按保守默认值降级，不中断初始化。
        """
        self.db_updater = db_updater or DBUpdater()
        self.model_binding_key = model_binding_key
        self.model_binding = get_agent_model_binding(model_binding_key)

    async def process_world_events(
        self,
        time_passed_minutes: int,
        location_id: str | None = None,
    ) -> dict[str, object]:
        """
        功能：处理后台世界事件。
        入参：time_passed_minutes；location_id。
        出参：dict[str, object]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        schedules = self.update_npc_schedules(time_passed_minutes)
        encounters = self.generate_random_encounters(
            time_passed_minutes=time_passed_minutes,
            location_id=location_id,
        )
        return {
            "time_passed_minutes": time_passed_minutes,
            "location_id": location_id or "unknown",
            "schedules": schedules,
            "encounters": encounters,
        }

    def update_npc_schedules(self, time_passed_minutes: int) -> dict[str, object]:
        """
        功能：更新 NPC 时间表。
        入参：time_passed_minutes。
        出参：dict[str, object]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_updater.upsert_world_state(
            "world.last_evolution_minutes",
            {"value": int(time_passed_minutes)},
        )
        return {"status": "updated", "delta_minutes": int(time_passed_minutes)}

    def generate_random_encounters(
        self,
        time_passed_minutes: int,
        location_id: str | None = None,
    ) -> list[dict[str, object]]:
        """
        功能：生成随机遭遇事件。
        入参：time_passed_minutes；location_id。
        出参：list[dict[str, object]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        location = location_id or "unknown"
        seed = f"{location}:{int(time_passed_minutes)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        if int(digest[:2], 16) % 5 != 0:
            return []

        encounter = {
            "encounter_id": f"enc_{digest[:8]}",
            "location_id": location,
            "threat_level": int(digest[2:4], 16) % 3 + 1,
            "description": "附近出现了可疑动静。",
        }
        self.db_updater.upsert_world_state("world.last_encounter", encounter)
        return [encounter]
