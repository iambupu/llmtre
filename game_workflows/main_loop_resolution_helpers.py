"""
MainEventLoop 动作结算辅助函数。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from game_workflows.pack_runtime import PackRuntimeContext, PackRuntimeResult
from game_workflows.quest_state_helpers import normalize_quest_states
from state.contracts.quest import QuestRuntimeState
from state.contracts.story_pack import StoryPackInteractionDef, StoryPackSceneDef
from state.contracts.trigger import TriggerDef
from tools.packs.trigger_effects import apply_trigger_effects
from tools.packs.trigger_evaluator import TriggerEvaluator, evaluate_triggers
from tools.roll.dice_roller import check_success, roll_d20, roll_dice

logger = logging.getLogger("Workflow.MainLoop")


@dataclass(frozen=True)
class _InteractionMatchFacts:
    """
    功能：承载一次动作匹配交互时需要比较的标准化文本事实。
    入参：target/hint/raw_input（str）：已标准化的目标、对象提示和玩家原文。
    出参：_InteractionMatchFacts。
    异常：dataclass 构造不做业务校验，调用方负责传入标准化文本。
    """

    target: str
    hint: str
    raw_input: str


@dataclass(frozen=True)
class _InteractionScoreWeights:
    """
    功能：承载交互候选在不同命中来源下的基础评分权重。
    入参：target/hint/raw_input（int）：target、object_hint 和玩家原文命中的基础分。
    出参：_InteractionScoreWeights。
    异常：dataclass 构造不做业务校验，调用方使用正整数常量。
    """

    target: int
    hint: int
    raw_input: int


@dataclass(frozen=True)
class _PackTriggerEvaluationInput:
    """
    功能：集中承载剧本包触发器评估所需输入，避免内部 helper 参数继续膨胀。
    入参：pack_triggers/state/action_result/physics_diff/current_scene_id/fired_ids/scene_switch_to/
        quest_states：触发器定义、回合状态、动作事实、确定性差异、场景与任务运行态。
    出参：_PackTriggerEvaluationInput。
    异常：dataclass 构造不做业务校验，评估函数负责字段降级和异常转换。
    """

    pack_triggers: dict[str, Any]
    state: Mapping[str, Any]
    action_result: dict[str, Any]
    physics_diff: dict[str, Any]
    current_scene_id: str
    fired_ids: set[str]
    scene_switch_to: str | None = None
    quest_states: list[QuestRuntimeState] | None = None


class PackTriggerRuntimeError(RuntimeError):
    """
    功能：标记剧本包触发器运行期契约错误，供主循环输出可见 diagnostics。
    入参：message（str）：错误说明。
    出参：PackTriggerRuntimeError。
    异常：构造时不额外抛出异常。
    """


def resolve_action_sync(
    loop: Any,
    state: Mapping[str, Any],
    pack_runtime: PackRuntimeContext | None = None,
) -> dict[str, Any]:
    """
    功能：同步执行动作结算逻辑；数值变化仅来自确定性规则和骰子工具。
    入参：loop（Any）：MainEventLoop 实例；state（Mapping[str, Any]）：已通过校验的动作状态；
        pack_runtime（PackRuntimeContext | None，默认 None）：剧本包运行期输入，
        缺省时从 state 提取。
    出参：dict[str, Any]，包含 physics_diff，供写计划消费。
    异常：事件总线钩子或只读查询异常向上抛出，由主循环调用方处理。
    """
    hooked_state = loop.event_bus.emit("on_action_pre", dict(state))
    action = cast(dict[str, Any], hooked_state["action_intent"])
    action_type = action["type"]
    pack_runtime = pack_runtime or PackRuntimeContext.from_flow_state(state)
    pack_scene = pack_runtime.scene
    trigger_action_result = _build_trigger_action_result(action, pack_scene)

    physics_diff, scene_switch_to = _resolve_action_physics(
        loop=loop,
        state=state,
        action=action,
        action_type=action_type,
        pack_scene=pack_scene,
    )

    logger.info("物理结算完成，结果: %s", physics_diff)
    result: dict[str, Any] = {"physics_diff": physics_diff}

    result.update(
        _build_pack_runtime_flow_patch(
            pack_runtime=pack_runtime,
            state=state,
            trigger_action_result=trigger_action_result,
            physics_diff=physics_diff,
            scene_switch_to=scene_switch_to,
        )
    )
    return result


def _resolve_action_physics(
    *,
    loop: Any,
    state: Mapping[str, Any],
    action: dict[str, Any],
    action_type: str,
    pack_scene: StoryPackSceneDef | None,
) -> tuple[dict[str, Any], str | None]:
    """
    功能：根据动作类型分派确定性物理结算，并返回可能发生的 pack 场景切换。
    入参：loop（Any）：MainEventLoop 实例；state（Mapping[str, Any]）：回合状态；
        action（dict[str, Any]）：动作意图；action_type（str）：动作类型；
        pack_scene（StoryPackSceneDef | None）：当前剧本包场景。
    出参：tuple[dict[str, Any], str | None]，分别为 physics_diff 与 scene_switch_to。
    异常：下游探针、事件总线或规则解析异常不捕获，交由主循环统一处理。
    """
    if action_type == "attack":
        return _resolve_attack_physics(loop=loop, state=state, action=action), None
    if action_type == "move":
        return _resolve_move_physics(
            loop=loop,
            state=state,
            action=action,
            pack_scene=pack_scene,
        )
    if action_type == "use_item":
        return _resolve_use_item_physics(loop=loop, action=action), None
    if action_type == "talk":
        return loop._resolve_configured_action("talk", {}), None
    if action_type in {"observe", "wait", "rest", "inspect", "interact", "skill"}:
        return loop._resolve_configured_action(action_type, {}), None
    if action_type in {"commit_sandbox", "discard_sandbox"}:
        return loop._resolve_configured_action(action_type, {}), None
    return {}, None


def _resolve_attack_physics(
    *,
    loop: Any,
    state: Mapping[str, Any],
    action: Mapping[str, Any],
) -> dict[str, Any]:
    """
    功能：执行攻击动作的命中与伤害结算，所有随机数来自确定性骰子工具。
    入参：loop（Any）：MainEventLoop 实例；state（Mapping[str, Any]）：回合状态；
        action（Mapping[str, Any]）：攻击动作，需包含 target_id。
    出参：dict[str, Any]，包含 attack_roll、attack_dc、attack_hit，命中时包含伤害差异。
    异常：target_id 缺失或探针异常会向上抛出，保持原调用链失败语义。
    """
    attacker: dict[str, Any] = dict(state.get("active_character") or {})
    target = loop.entity_probes.get_character_stats(str(action["target_id"]))
    rng = loop._build_action_rng(state)
    attack_rules = loop.rules.get("resolution", {}).get("attack", {})
    attacker_strength = loop._to_int(attacker.get("strength", 10), 10)
    target_agility = loop._to_int(target.get("agility", 10), 10) if target else 10
    attack_roll = roll_d20(modifier=attacker_strength, rng=rng)
    base_dc = loop._to_int(attack_rules.get("base_dc", 10), 10)
    agility_divisor = max(1, loop._to_int(attack_rules.get("agility_divisor", 2), 2))
    attack_dc = base_dc + target_agility // agility_divisor
    attack_hit = check_success(attack_roll, attack_dc)
    physics_diff: dict[str, Any] = {
        "attack_roll": attack_roll,
        "attack_dc": attack_dc,
        "attack_hit": attack_hit,
    }
    if attack_hit:
        damage_dice = str(attack_rules.get("damage_dice", "d6"))
        damage_roll = roll_dice(damage_dice, rng=rng)[0]
        strength_divisor = max(
            1,
            loop._to_int(attack_rules.get("strength_damage_divisor", 3), 3),
        )
        min_damage = max(1, loop._to_int(attack_rules.get("min_damage", 1), 1))
        damage = max(min_damage, damage_roll + attacker_strength // strength_divisor)
        physics_diff["damage_roll"] = damage_roll
        physics_diff["target_hp_delta"] = -damage
    return physics_diff


def _resolve_move_physics(
    *,
    loop: Any,
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    pack_scene: StoryPackSceneDef | None,
) -> tuple[dict[str, Any], str | None]:
    """
    功能：执行移动结算，处理剧本包出口匹配、位置差异与 location_changed 事件。
    入参：loop（Any）：MainEventLoop 实例；state（Mapping[str, Any]）：回合状态；
        action（Mapping[str, Any]）：移动动作；
        pack_scene（StoryPackSceneDef | None）：当前 pack 场景。
    出参：tuple[dict[str, Any], str | None]，physics_diff 与可选 scene_switch_to。
    异常：事件总线 emit 或配置动作解析异常向上抛出，保持事务外层可观测。
    """
    target_location = action.get("parameters", {}).get("location_id", "unknown")
    # Pack 场景运行态可能先于角色 SQLite location 生效；移动事件的 from
    # 以本回合当前 pack scene 为准，避免首回合把历史位置投递给事件总线。
    active_character = state.get("active_character") or {}
    current_scene_id = str(state.get("current_scene_id") or "").strip()
    current_location = (
        current_scene_id
        if pack_scene is not None and current_scene_id
        else str(active_character.get("location", "unknown"))
    )
    target_location, scene_switch_to = _resolve_pack_exit_target(
        state=state,
        pack_scene=pack_scene,
        current_location=current_location,
        target_location=target_location,
    )
    physics_diff = loop._resolve_configured_action("move", {"location_id": target_location})
    # 将位置变更写入结算差异，下游 build_write_plan 据此生成 DB 写操作
    # db_updater.apply_diff() 通过 diff["location_id"] 读取目标位置；
    # location_change 保留为结构化事件供外环消费。
    physics_diff["location_id"] = target_location
    physics_diff["location_change"] = {
        "from": current_location,
        "to": target_location,
    }
    # 通过事件总线发出位置变更事件，供外环和其他订阅者消费。
    loop.event_bus.emit(
        "location_changed",
        {
            "entity_id": state.get("active_character_id"),
            "from_location": current_location,
            "to_location": target_location,
        },
    )
    return physics_diff, scene_switch_to


def _resolve_pack_exit_target(
    *,
    state: Mapping[str, Any],
    pack_scene: StoryPackSceneDef | None,
    current_location: str,
    target_location: Any,
) -> tuple[Any, str | None]:
    """
    功能：将移动目标映射为剧本包出口目标场景，并记录未满足条件的降级路径。
    入参：state（Mapping[str, Any]）：回合状态；pack_scene（StoryPackSceneDef | None）：当前场景；
        current_location（str）：事件来源位置；target_location（Any）：玩家请求的目标。
    出参：tuple[Any, str | None]，返回实际移动目标与可选 scene_switch_to；
        条件未满足时返回 current_location，保证防御性降级不写入锁定场景。
    异常：不捕获 _find_pack_exit/_check_exit_conditions 的异常，保留运行期契约错误可见性。
    """
    if pack_scene is None:
        return target_location, None

    matched_exit = _find_pack_exit(str(target_location), pack_scene)
    if matched_exit is None:
        logger.info(
            "移动目标 '%s' 未匹配当前场景任何出口，按普通移动处理",
            target_location,
        )
        return target_location, None

    # 检查出口条件：空列表自动通过；非空列表需全部 state_flag 命中。
    if _check_exit_conditions(matched_exit, state):
        old_scene_id = str(state.get("current_scene_id", ""))
        new_scene_id = matched_exit.target_scene_id
        logger.info(
            "场景切换: %s -> %s (exit: %s)",
            old_scene_id,
            new_scene_id,
            matched_exit.label,
        )
        return new_scene_id, new_scene_id

    logger.warning(
        "出口条件未满足: %s -> %s (exit: %s)",
        current_location,
        matched_exit.target_scene_id,
        matched_exit.label,
    )
    # 正常路径会在校验层拦截条件不满足的移动；这里保留事务边界内的
    # 防御性降级，避免未来调用方绕过校验时把未解锁场景写入角色位置。
    return current_location, None


def _resolve_use_item_physics(
    *,
    loop: Any,
    action: Mapping[str, Any],
) -> dict[str, Any]:
    """
    功能：根据物品定义 effects 生成 HP/MP 差异，并记录被消耗的物品 ID。
    入参：loop（Any）：MainEventLoop 实例；action（Mapping[str, Any]）：use_item 动作。
    出参：dict[str, Any]，可能包含 hp_delta、mp_delta、consumed_item_id。
    异常：物品定义读取异常向上抛出；非法 effect 条目会被跳过。
    """
    physics_diff: dict[str, Any] = {}
    item_id = action.get("parameters", {}).get("item_id")
    item_definition = loop.entity_probes.get_item_definition(str(item_id))
    if not item_definition:
        return physics_diff
    for effect in item_definition.get("effects", []):
        if not isinstance(effect, dict):
            continue
        target_attribute = effect.get("target_attribute")
        value = loop._to_int(effect.get("value", 0))
        if target_attribute == "hp":
            physics_diff["hp_delta"] = physics_diff.get("hp_delta", 0) + value
        elif target_attribute == "mp":
            physics_diff["mp_delta"] = physics_diff.get("mp_delta", 0) + value
    physics_diff["consumed_item_id"] = str(item_id)
    return physics_diff


def _build_pack_runtime_flow_patch(
    *,
    pack_runtime: PackRuntimeContext,
    state: Mapping[str, Any],
    trigger_action_result: dict[str, Any],
    physics_diff: dict[str, Any],
    scene_switch_to: str | None,
) -> dict[str, Any]:
    """
    功能：评估剧本包触发器并生成写回 FlowState 的触发器、任务与诊断 patch。
    入参：pack_runtime（PackRuntimeContext）：当前 pack 运行态；
        state（Mapping[str, Any]）：回合状态；trigger_action_result（dict[str, Any]）：动作摘要；
        physics_diff（dict[str, Any]）：确定性结算差异；
        scene_switch_to（str | None）：场景切换目标。
    出参：dict[str, Any]，PackRuntimeResult.to_flow_patch 产生的字段集合。
    异常：触发器评估异常会被捕获并写入 pack_runtime_errors，保持回合不中断。
    """
    # A2-Plus: 剧本包触发器评估与任务推进
    trigger_events: list[dict[str, Any]] = []
    new_fired_ids: set[str] = set(pack_runtime.fired_trigger_ids)
    runtime_quest_states = normalize_quest_states(
        pack_runtime.quest_states,
        pack_runtime.quests,
    )
    had_existing_quest_states = pack_runtime.has_existing_quest_states
    changed_quest_ids: set[str] = set()
    pack_runtime_errors: list[dict[str, Any]] = []
    if pack_runtime.triggers:
        try:
            trigger_events, additional_fired = _evaluate_pack_triggers(
                _PackTriggerEvaluationInput(
                    pack_triggers=pack_runtime.triggers,
                    state=state,
                    action_result=trigger_action_result,
                    physics_diff=physics_diff,
                    current_scene_id=str(state.get("current_scene_id", "")),
                    fired_ids=new_fired_ids,
                    scene_switch_to=scene_switch_to,
                    quest_states=runtime_quest_states,
                )
            )
            new_fired_ids.update(additional_fired)
            effect_result = apply_trigger_effects(
                trigger_events=trigger_events,
                pack_triggers=pack_runtime.triggers,
                physics_diff=physics_diff,
                quest_states=runtime_quest_states,
                active_character_id=str(state.get("active_character_id") or ""),
            )
            changed_quest_ids.update(effect_result.changed_quest_ids)
        except Exception as exc:
            logger.warning("剧本包触发器评估失败: %s", exc, exc_info=True)
            pack_runtime_errors.append(
                {
                    "stage": "pack.trigger_runtime",
                    "error": str(exc),
                }
            )
    quest_state_dicts = [item.model_dump(mode="json") for item in runtime_quest_states]
    if changed_quest_ids:
        quest_updates = [
            item for item in quest_state_dicts if item.get("quest_id") in changed_quest_ids
        ]
    else:
        quest_updates = [] if had_existing_quest_states else quest_state_dicts
    pack_result = PackRuntimeResult(
        trigger_events=trigger_events,
        quest_states=quest_state_dicts,
        quest_updates=quest_updates,
        fired_trigger_ids=sorted(new_fired_ids),
        scene_switch_to=scene_switch_to,
        pack_runtime_errors=pack_runtime_errors,
    )
    return pack_result.to_flow_patch()


def _find_pack_exit(location_id: str, pack_scene: StoryPackSceneDef) -> Any | None:
    """
    功能：在剧本包场景出口中查找匹配用户目标 location_id 的出口定义。
    入参：location_id（str）：用户移动目标（scene_id、label 或别名）；
         pack_scene（StoryPackSceneDef）：当前剧本包场景。
    出参：StoryPackExitDef | None，匹配成功返回出口定义，否则返回 None。
    异常：不抛异常；无 exits 或字段缺失时保守返回 None。
    """
    normalized = location_id.strip().lower()
    for exit_def in pack_scene.exits:
        if exit_def.target_scene_id == location_id:
            return exit_def
        if exit_def.label.strip().lower() == normalized:
            return exit_def
        if any(alias.strip().lower() == normalized for alias in exit_def.aliases):
            return exit_def
    return None


def _check_exit_conditions(exit_def: Any, state: Mapping[str, Any]) -> bool:
    """
    功能：检查剧本包出口的 conditions 是否满足。
    入参：exit_def（StoryPackExitDef）：剧本包出口定义；
         state（dict[str, Any]）：当前回合状态。
    出参：bool，条件为空或全部满足时返回 True。
    异常：不抛异常；字段缺失保守放行（返回 True）。
    说明：A2-Plus first pass 采用简单字符串匹配——
         conditions 列表为空则自动通过；
         否则检查 state 中的 state_flags 是否包含所有 condition 字符串。
    """
    conditions = getattr(exit_def, "conditions", None)
    if not conditions:
        return True
    if not isinstance(conditions, list):
        return True
    if len(conditions) == 0:
        return True
    active_character = state.get("active_character") or {}
    state_flags: list[str] = list(active_character.get("state_flags", []))
    if not isinstance(state_flags, list):
        state_flags = []
    state_flag_set = set(f.strip().lower() for f in state_flags)
    for cond in conditions:
        if not isinstance(cond, str):
            continue
        if cond.strip().lower() not in state_flag_set:
            return False
    return True


def _normalize_text(value: Any) -> str:
    """
    功能：将交互匹配输入压成大小写无关的比较文本。
    入参：value（Any）：可能为空、ID、标签或用户原文片段。
    出参：str，去除首尾空白并转小写后的文本；空值返回空字符串。
    异常：不抛异常；无法表达为业务文本的对象仅使用 str() 的降级结果。
    """
    return str(value or "").strip().lower()


def _matches_interaction(
    interaction: StoryPackInteractionDef,
    *,
    action_type: str,
    raw_input: str,
    target_id: str,
    object_hint: str,
) -> bool:
    """
    功能：判断一次玩家动作是否命中剧本场景中的可交互定义。
    入参：interaction（StoryPackInteractionDef）：候选交互；
        action_type（str）：NLU 解析出的动作类型；
        raw_input（str）：玩家原始输入，用于兜底文本匹配；
        target_id（str）：解析链路携带的目标 ID；
        object_hint（str）：参数中的对象提示文本。
    出参：bool，命中 interaction_id/label/target_ref/aliases 任一匹配条件返回 True。
    异常：不抛异常；空字段被标准化为空字符串并自然跳过。
    """
    return (
        _score_interaction_match(
            interaction,
            action_type=action_type,
            raw_input=raw_input,
            target_id=target_id,
            object_hint=object_hint,
        )
        > 0
    )


def _score_interaction_match(
    interaction: StoryPackInteractionDef,
    *,
    action_type: str,
    raw_input: str,
    target_id: str,
    object_hint: str,
) -> int:
    """
    功能：为动作与剧本交互的匹配程度打分，长标签/长别名优先于通用 target_ref。
    入参：interaction（StoryPackInteractionDef）：候选交互；action_type/raw_input/target_id/object_hint：
        NLU 动作事实，语义同 _matches_interaction。
    出参：int，0 表示不匹配；分数越高表示越具体，供同物体多交互时选择正确入口。
    异常：不抛异常；缺失字段按空字符串降级。
    """
    if action_type != "interact" and interaction.kind != action_type:
        return 0
    facts = _InteractionMatchFacts(
        target=_normalize_text(target_id),
        hint=_normalize_text(object_hint),
        raw_input=_normalize_text(raw_input),
    )
    # 先比较交互 ID、标签和别名，再用 target_ref 兜底，避免同物体多个交互被通用目标抢走。
    specific_score = max(
        (
            _score_interaction_candidate(
                candidate,
                facts=facts,
                weights=_InteractionScoreWeights(target=1000, hint=800, raw_input=600),
            )
            for candidate in [interaction.interaction_id, interaction.label, *interaction.aliases]
        ),
        default=0,
    )
    target_ref_score = _score_interaction_candidate(
        interaction.target_ref or "",
        facts=facts,
        weights=_InteractionScoreWeights(target=100, hint=80, raw_input=60),
    )
    return max(specific_score, target_ref_score)


def _score_interaction_candidate(
    candidate: str,
    *,
    facts: _InteractionMatchFacts,
    weights: _InteractionScoreWeights,
) -> int:
    """
    功能：对单个交互候选文本评分，集中处理 target、object_hint 和原始输入三种命中来源。
    入参：candidate（str）：候选 ID/标签/别名；
        facts（_InteractionMatchFacts）：已标准化的动作事实；
        weights（_InteractionScoreWeights）：不同来源的基础权重。
    出参：int，0 表示未命中；命中时返回基础权重加候选长度。
    异常：不抛异常；空候选直接返回 0。
    """
    normalized = _normalize_text(candidate)
    if not normalized:
        return 0
    scores: list[int] = []
    if facts.target and facts.target == normalized:
        scores.append(weights.target + len(normalized))
    if _text_overlaps(facts.hint, normalized):
        scores.append(weights.hint + len(normalized))
    if facts.raw_input and normalized in facts.raw_input:
        scores.append(weights.raw_input + len(normalized))
    return max(scores, default=0)


def _text_overlaps(left: str, right: str) -> bool:
    """
    功能：判断两个已标准化文本是否存在包含式重叠。
    入参：left/right（str）：已标准化文本。
    出参：bool，两者非空且任一方包含另一方时为 True。
    异常：不抛异常。
    """
    return bool(left and right and (left == right or left in right or right in left))


def _resolve_interaction_id(
    action: Mapping[str, Any],
    pack_scene: StoryPackSceneDef | None,
) -> str:
    """
    功能：从动作参数或场景可交互定义中解析剧本 interaction_id。
    入参：action（Mapping[str, Any]）：NLU 产出的动作候选；
        pack_scene（StoryPackSceneDef | None）：当前剧本场景，缺失时只能使用显式参数。
    出参：str，优先返回显式 interaction_id，未命中或无场景时返回空字符串。
    异常：不抛异常；非法 parameters 按空映射处理，匹配失败走空字符串降级。
    """
    parameters = action.get("parameters")
    params = parameters if isinstance(parameters, Mapping) else {}
    explicit = str(params.get("interaction_id") or "").strip()
    if explicit:
        return explicit
    if pack_scene is None:
        return ""

    action_type = str(action.get("type") or "")
    raw_input = str(action.get("raw_input") or "")
    target_id = str(action.get("target_id") or "")
    object_hint = str(params.get("object_hint") or "")
    best_match = ""
    best_score = 0
    for interaction in pack_scene.interactables:
        score = _score_interaction_match(
            interaction,
            action_type=action_type,
            raw_input=raw_input,
            target_id=target_id,
            object_hint=object_hint,
        )
        if score > best_score:
            best_match = interaction.interaction_id
            best_score = score
    return best_match


def _build_trigger_action_result(
    action: Mapping[str, Any],
    pack_scene: StoryPackSceneDef | None,
) -> dict[str, Any]:
    """
    功能：为剧本触发器构造最小动作事实，保留动作、目标与交互标识。
    入参：action（Mapping[str, Any]）：本回合解析出的动作；
        pack_scene（StoryPackSceneDef | None）：当前剧本场景，用于补全交互类型。
    出参：dict[str, Any]，供 TriggerEvaluator 判断条件，不直接写入游戏状态。
    异常：不抛异常；缺少目标、交互或原文时省略对应字段作为降级路径。
    """
    action_type = str(action.get("type") or "")
    result: dict[str, Any] = {"action": action_type}
    target_id = str(action.get("target_id") or "").strip()
    if target_id:
        result["target_id"] = target_id
    interaction_id = _resolve_interaction_id(action, pack_scene)
    if interaction_id:
        result["interaction_id"] = interaction_id
        if action_type == "interact" and pack_scene is not None:
            matched = next(
                (
                    item
                    for item in pack_scene.interactables
                    if item.interaction_id == interaction_id
                ),
                None,
            )
            if matched is not None and matched.kind in {"observe", "talk", "inspect"}:
                result["action"] = matched.kind
    raw_input = str(action.get("raw_input") or "").strip()
    if raw_input:
        result["raw_input"] = raw_input
    return result


def _evaluate_pack_triggers(
    request: _PackTriggerEvaluationInput,
) -> tuple[list[dict[str, Any]], set[str]]:
    """
    功能：评估剧本包触发器，返回本轮触发事件与合并后的已触发 ID 集合。
    入参：request（_PackTriggerEvaluationInput）：触发器定义、回合状态、动作事实、场景与任务运行态。
    出参：tuple[list[dict[str, Any]], set[str]]，事件 payload 与最新 fired_id 集合。
    异常：触发器反序列化失败会记录日志并返回空事件；evaluate_triggers 异常由调用方捕获。
    """
    trigger_defs: list[Any] = []
    for trigger_key, raw in request.pack_triggers.items():
        try:
            if isinstance(raw, TriggerDef):
                trigger_defs.append(raw)
            elif isinstance(raw, dict):
                trigger_defs.append(TriggerDef.model_validate(raw))
        except Exception as exc:
            raise PackTriggerRuntimeError(f"触发器定义反序列化失败: {trigger_key}: {exc}") from exc

    session_metadata: dict[str, Any] = {
        "fired_trigger_ids": sorted(request.fired_ids),
        "active_character": dict(request.state.get("active_character") or {}),
    }
    include_current_enter_scene = _should_evaluate_current_enter_scene(
        trigger_defs=trigger_defs,
        current_scene_id=request.current_scene_id,
        fired_ids=request.fired_ids,
        scene_switch_to=request.scene_switch_to,
    )

    all_events_raw = evaluate_triggers(
        trigger_defs=trigger_defs,
        session_metadata=session_metadata,
        current_scene_id=request.current_scene_id,
        action_result=request.action_result,
        quest_states=request.quest_states or [],
        runtime_events=_build_runtime_event_contexts(
            state=request.state,
            action_result=request.action_result,
            physics_diff=request.physics_diff,
            current_scene_id=request.current_scene_id,
            scene_switch_to=request.scene_switch_to,
        ),
        # 场景进入触发器的补评估只服务于“直接运行主循环且尚未创建 session 初始事件”的场景；
        # 移动回合仍由下方目标场景补触发，避免离开源场景时播放源场景开场。
        include_enter_scene=include_current_enter_scene,
    )

    if request.scene_switch_to and request.scene_switch_to != request.current_scene_id:
        evaluator_obj = TriggerEvaluator(trigger_defs, request.fired_ids)
        enter_events = evaluator_obj.evaluate("enter_scene", {"scene_id": request.scene_switch_to})
        existing_ids = {e.trigger_id for e in all_events_raw}
        for evt in enter_events:
            if evt.trigger_id not in existing_ids:
                all_events_raw.append(evt)

    new_fired = set(request.fired_ids)
    event_dicts: list[dict[str, Any]] = []
    for evt in all_events_raw:
        new_fired.add(evt.trigger_id)
        event_dicts.append(evt.model_dump())

    logger.info(
        "触发器评估完成: 场景=%s, 触发=%d 个事件",
        request.current_scene_id,
        len(event_dicts),
    )
    return event_dicts, new_fired


def _should_evaluate_current_enter_scene(
    *,
    trigger_defs: list[TriggerDef],
    current_scene_id: str,
    fired_ids: set[str],
    scene_switch_to: str | None,
) -> bool:
    """
    功能：判断本轮是否需要补评估当前场景的 enter_scene 触发器。
    入参：trigger_defs（list[TriggerDef]）：已反序列化的触发器定义；
        current_scene_id（str）：当前场景；fired_ids（set[str]）：session 已触发 ID；
        scene_switch_to（str | None）：本轮移动目标，存在时由目标场景单独补触发。
    出参：bool，True 表示当前场景仍有未触发的进入触发器需要评估。
    异常：不抛异常；缺失或复杂 conditions 按不需要补评估处理，避免误播源场景开场。
    """
    if scene_switch_to and scene_switch_to != current_scene_id:
        return False
    for trigger_def in trigger_defs:
        if trigger_def.type != "enter_scene":
            continue
        if trigger_def.once and trigger_def.trigger_id in fired_ids:
            continue
        scene_id = trigger_def.conditions.get("scene_id")
        if scene_id != current_scene_id:
            continue
        # 非 once 的 enter_scene 没有可靠的历史去重语义，只允许在完全没有触发历史时补首轮。
        if not trigger_def.once and fired_ids:
            continue
        return True
    return False


def _build_runtime_event_contexts(
    state: Mapping[str, Any],
    action_result: dict[str, Any],
    physics_diff: dict[str, Any],
    current_scene_id: str,
    scene_switch_to: str | None,
) -> list[dict[str, Any]]:
    """
    功能：把本回合确定性结算结果转换为 Story Pack event 触发器可匹配的事件上下文。
    入参：state（Mapping[str, Any]）：回合状态；action_result（dict[str, Any]）：动作摘要；
        physics_diff（dict[str, Any]）：确定性状态差异；current_scene_id（str）：当前场景；
        scene_switch_to（str | None）：若本回合通过出口切换场景则为目标场景。
    出参：list[dict[str, Any]]，每项包含 event_name 与稳定标量字段。
    异常：不抛异常；缺失字段会被省略，确保事件触发器不能依赖脏上下文。
    """
    action = str(action_result.get("action") or "").strip()
    events: list[dict[str, Any]] = [
        {
            "event_name": "action_resolved",
            "scene_id": current_scene_id,
            "action": action,
            "character_id": str(state.get("active_character_id") or ""),
        }
    ]
    for key in ("target_id", "interaction_id"):
        value = str(action_result.get(key) or "").strip()
        if value:
            events[0][key] = value
    if scene_switch_to and scene_switch_to != current_scene_id:
        events.append(
            {
                "event_name": "scene_changed",
                "scene_id": current_scene_id,
                "from_scene_id": current_scene_id,
                "to_scene_id": scene_switch_to,
                "action": action,
                "character_id": str(state.get("active_character_id") or ""),
            }
        )
    consumed_item_id = str(physics_diff.get("consumed_item_id") or "").strip()
    if consumed_item_id:
        events.append(
            {
                "event_name": "item_consumed",
                "scene_id": current_scene_id,
                "item_id": consumed_item_id,
                "action": action,
                "character_id": str(state.get("active_character_id") or ""),
            }
        )
    return events
