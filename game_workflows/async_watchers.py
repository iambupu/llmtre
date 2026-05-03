import logging
from collections import defaultdict
from typing import Any, cast

from llama_index.core.workflow import Context, StartEvent, StopEvent, Workflow, step

from agents.evolution_agent import EvolutionAgent
from core.runtime_logging import ensure_runtime_logging
from game_workflows.event_schemas import (
    AchievementUnlockedEvent,
    StateChangedEvent,
    TurnEndedEvent,
    WorldEvolutionEvent,
)
from game_workflows.main_loop_config import load_main_loop_rules
from tools.sqlite_db.db_updater import DBUpdater

ensure_runtime_logging()
logger = logging.getLogger("Workflow.AsyncWatchers")

class GlobalEventWorkflow(Workflow):
    """
    异步事件观察者 (外环)：负责处理孤立的、旁路的、松耦合的逻辑
    """

    def __init__(self, *args: Any, db_path: str | None = None, **kwargs: Any):
        """
        功能：初始化对象状态与依赖。
        入参：*args；db_path；**kwargs。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        super().__init__(*args, **kwargs)
        self._unlocked_achievements: dict[str, set[str]] = defaultdict(set)
        self.db_updater = DBUpdater(db_path=db_path) if db_path else DBUpdater()
        self.rules = load_main_loop_rules()
        self.evolution_agent = EvolutionAgent(db_updater=self.db_updater)

    @step
    async def start(
        self, ctx: Context, ev: StartEvent
    ) -> (
        TurnEndedEvent
        | StateChangedEvent
        | WorldEvolutionEvent
        | AchievementUnlockedEvent
        | StopEvent
    ):
        """
        功能：工作流启动入口：将 StartEvent 中承载的外环事件路由给具体处理节点。
        入参：ctx；ev。
        出参：TurnEndedEvent | StateChangedEvent | WorldEvolutionEvent |
        AchievementUnlockedEvent | StopEvent。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("外环事件观察者工作流已启动。")
        event_name = ev.get("event_name")
        payload = ev.get("payload", {})

        if event_name == "turn_ended":
            return TurnEndedEvent(**payload)
        if event_name == "state_changed":
            return StateChangedEvent(**payload)
        if event_name == "world_evolution":
            return WorldEvolutionEvent(**payload)
        if event_name == "achievement_unlocked":
            return AchievementUnlockedEvent(**payload)
        return StopEvent(result="ignored_unknown_outer_event")

    @step
    async def handle_turn_ended(self, ctx: Context, ev: TurnEndedEvent) -> StopEvent:
        """
        功能：监听回合结束事件，触发后续审计或小结。
        入参：ctx；ev。
        出参：StopEvent。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(f"外环监听到回合结束: Turn {ev.turn_id}")
        # 这里可以触发对话摘要生成、Token 统计等
        return StopEvent(result=f"Turn {ev.turn_id} audit completed.")

    @step
    async def handle_state_changed(
        self, ctx: Context, ev: StateChangedEvent
    ) -> AchievementUnlockedEvent | StopEvent:
        """
        功能：监听状态变更，触发成就检测或环境连锁反应。
        入参：ctx；ev。
        出参：AchievementUnlockedEvent | StopEvent。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(f"外环监听到状态变更: Entity {ev.entity_id}, Diff: {ev.diff}")
        achievement = self._derive_achievement_event(ev)
        if achievement is not None:
            if self._mark_achievement_once(achievement.entity_id, achievement.achievement_id):
                return achievement
            logger.info(
                "外环忽略重复成就: achievement=%s entity=%s",
                achievement.achievement_id,
                achievement.entity_id,
            )
        return StopEvent(result=f"StateChanged entity={ev.entity_id} observed.")

    @step
    async def handle_world_evolution(self, ctx: Context, ev: WorldEvolutionEvent) -> StopEvent:
        """
        功能：监听世界演化事件，触发后台 NPC 势力变动推演。
        入参：ctx；ev。
        出参：StopEvent。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(f"外环启动世界演化推演: {ev.time_passed_minutes} minutes passed.")
        evolution = await self.evolution_agent.process_world_events(
            time_passed_minutes=ev.time_passed_minutes,
            location_id=ev.location_id,
        )
        self.db_updater.upsert_world_state("world.last_evolution_result", evolution)
        encounters = evolution.get("encounters", [])
        return StopEvent(
            result=(
                f"WorldEvolution +{ev.time_passed_minutes}min processed."
                f" encounters={len(encounters) if isinstance(encounters, list) else 0}"
            )
        )

    @step
    async def handle_achievement_unlocked(
        self, ctx: Context, ev: AchievementUnlockedEvent
    ) -> StopEvent:
        """
        功能：监听最小成就事件，记录外环业务执行结果。
        入参：ctx；ev。
        出参：StopEvent。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        rewards = self._achievement_rewards()
        reward_raw = rewards.get(ev.achievement_id, {})
        reward = cast(dict[str, object], reward_raw if isinstance(reward_raw, dict) else {})
        recorded = self.db_updater.record_achievement_unlock(
            entity_id=ev.entity_id,
            achievement_id=ev.achievement_id,
            description=ev.description,
            reward=reward,
        )
        if not recorded:
            logger.info(
                "外环成就已存在（跳过重复写入）: achievement=%s entity=%s",
                ev.achievement_id,
                ev.entity_id,
            )
            return StopEvent(result=f"Achievement {ev.achievement_id} already unlocked.")

        if reward:
            try:
                self.db_updater.apply_diff(ev.entity_id, reward, use_shadow=False)
            except Exception as error:  # noqa: BLE001
                if "no such table" in str(error):
                    # 降级路径：外环可在仅有运行期表的最小库中执行，奖励写入缺实体表时跳过即可。
                    logger.info(
                        "外环成就奖励跳过: achievement=%s entity=%s reason=%s",
                        ev.achievement_id,
                        ev.entity_id,
                        error,
                    )
                    logger.info(
                        "外环成就解锁: achievement=%s entity=%s desc=%s reward=%s",
                        ev.achievement_id,
                        ev.entity_id,
                        ev.description,
                        reward,
                    )
                    return StopEvent(
                        result=(
                            f"Achievement {ev.achievement_id} unlocked "
                            f"for {ev.entity_id}."
                        )
                    )
                logger.warning(
                    "外环成就奖励写入失败（已忽略）: achievement=%s entity=%s err=%s",
                    ev.achievement_id,
                    ev.entity_id,
                    error,
                )
        logger.info(
            "外环成就解锁: achievement=%s entity=%s desc=%s reward=%s",
            ev.achievement_id,
            ev.entity_id,
            ev.description,
            reward,
        )
        return StopEvent(result=f"Achievement {ev.achievement_id} unlocked for {ev.entity_id}.")

    def _derive_achievement_event(self, ev: StateChangedEvent) -> AchievementUnlockedEvent | None:
        """
        功能：执行 `_derive_achievement_event` 相关业务逻辑。
        入参：ev。
        出参：AchievementUnlockedEvent | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        target_hp_delta = int(ev.diff.get("target_hp_delta", 0))
        if target_hp_delta < 0:
            return AchievementUnlockedEvent(
                achievement_id="first_blood",
                entity_id=ev.entity_id,
                description="首次对敌对目标造成了有效伤害。",
            )

        state_flags = ev.diff.get("state_flags_add")
        if isinstance(state_flags, list) and "observed_surroundings" in state_flags:
            return AchievementUnlockedEvent(
                achievement_id="keen_observer",
                entity_id=ev.entity_id,
                description="完成了一次有效环境侦察。",
            )
        return None

    def _mark_achievement_once(self, entity_id: str, achievement_id: str) -> bool:
        """
        功能：执行 `_mark_achievement_once` 相关业务逻辑。
        入参：entity_id；achievement_id。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if self.db_updater.is_achievement_unlocked(entity_id, achievement_id):
            return False
        unlocked = self._unlocked_achievements[entity_id]
        if achievement_id in unlocked:
            return False
        unlocked.add(achievement_id)
        return True

    def _achievement_rewards(self) -> dict[str, dict[str, int]]:
        """
        功能：执行 `_achievement_rewards` 相关业务逻辑。
        入参：无。
        出参：dict[str, dict[str, int]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        configured = self.rules.get("outer_loop", {}).get("achievement_rewards", {})
        if not isinstance(configured, dict):
            return {}
        rewards: dict[str, dict[str, int]] = {}
        for achievement_id, raw in configured.items():
            if not isinstance(raw, dict):
                continue
            rewards[str(achievement_id)] = {
                key: int(value)
                for key, value in raw.items()
                if key in {"hp_delta", "mp_delta"} and isinstance(value, int)
            }
        return rewards

class OuterLoopBridge:
    """外环桥接接口：内环通过它向外环投递最小事件。"""

    async def emit_state_changed(
        self, event: StateChangedEvent
    ) -> Any:  # pragma: no cover - interface
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        raise NotImplementedError

    async def emit_turn_ended(self, event: TurnEndedEvent) -> Any:  # pragma: no cover - interface
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        raise NotImplementedError

    async def emit_world_evolution(
        self, event: WorldEvolutionEvent
    ) -> Any:  # pragma: no cover - interface
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        raise NotImplementedError


class NoOpOuterLoopBridge(OuterLoopBridge):
    """默认安全桥接器：只记录日志，不触发外环执行。"""

    async def emit_state_changed(self, event: StateChangedEvent) -> None:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("外环桥接(最小): state_changed entity=%s", event.entity_id)

    async def emit_turn_ended(self, event: TurnEndedEvent) -> None:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("外环桥接(最小): turn_ended turn=%s", event.turn_id)

    async def emit_world_evolution(self, event: WorldEvolutionEvent) -> None:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(
            "外环桥接(最小): world_evolution +%smin loc=%s",
            event.time_passed_minutes,
            event.location_id or "unknown",
        )


class WorkflowOuterLoopBridge(OuterLoopBridge):
    """将内环事件投递给 LlamaIndex 外环工作流。"""

    def __init__(self, workflow: Workflow | None = None):
        # 默认创建独立工作流实例，避免跨会话共享内存态（如成就去重缓存）。
        """
        功能：初始化对象状态与依赖。
        入参：workflow。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.workflow = workflow or GlobalEventWorkflow(timeout=60, verbose=True)

    async def _dispatch(self, event: Any) -> Any:
        """
        功能：执行 `_dispatch` 相关业务逻辑。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        event_name = self._event_name(event)
        payload = self._event_payload(event)
        handler = self.workflow.run(
            start_event=cast(Any, StartEvent)(
                event_name=event_name,
                payload=payload,
            )
        )
        return await handler

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        """
        功能：执行 `_event_payload` 相关业务逻辑。
        入参：event。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if hasattr(event, "model_dump"):
            dumped = event.model_dump()
            if isinstance(dumped, dict):
                return cast(dict[str, Any], dumped)
            return {}
        if isinstance(event, dict):
            return cast(dict[str, Any], event)
        return {}

    @staticmethod
    def _event_name(event: Any) -> str:
        """
        功能：执行 `_event_name` 相关业务逻辑。
        入参：event。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if isinstance(event, TurnEndedEvent):
            return "turn_ended"
        if isinstance(event, StateChangedEvent):
            return "state_changed"
        if isinstance(event, WorldEvolutionEvent):
            return "world_evolution"
        if isinstance(event, AchievementUnlockedEvent):
            return "achievement_unlocked"
        return "unknown"

    async def emit_state_changed(self, event: StateChangedEvent) -> Any:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await self._dispatch(event)

    async def emit_turn_ended(self, event: TurnEndedEvent) -> Any:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await self._dispatch(event)

    async def emit_world_evolution(self, event: WorldEvolutionEvent) -> Any:
        """
        功能：投递事件到下游处理链路。
        入参：event。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await self._dispatch(event)
