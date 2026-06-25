"""
A3 golden Story Pack 分支验收工具。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from state.contracts.quest import QuestDef, QuestStage
from state.contracts.story_pack import StoryPackBundle, StoryPackSceneDef
from state.contracts.trigger import TriggerDef
from tools.packs.registry import StoryPackValidationError, validate_story_pack

A3_BRANCH_GROUP_KEY = "a3_branch_group"
A3_BRANCH_VALUE_KEY = "branch_value"
A3_MERGE_STAGE_KEY = "merge_stage_id"


@dataclass(frozen=True)
class A3BranchGroupSummary:
    """
    功能：描述一个通过 A3 校验的任务分支组。
    入参：quest_id（str）：任务 ID；branch_group（str）：分支组 ID；
        branch_stage_ids（list[str]）：互斥分支阶段；branch_values（list[str]）：分支取值；
        merge_stage_id（str）：汇合阶段；branch_trigger_ids（list[str]）：进入分支的触发器；
        merge_trigger_ids（list[str]）：从分支回到汇合阶段的触发器。
    出参：A3BranchGroupSummary。
    异常：dataclass 构造不做业务校验，调用方在 verify 阶段收集诊断。
    """

    quest_id: str
    branch_group: str
    branch_stage_ids: list[str]
    branch_values: list[str]
    merge_stage_id: str
    branch_trigger_ids: list[str]
    merge_trigger_ids: list[str]


@dataclass(frozen=True)
class A3VerificationResult:
    """
    功能：承载 A3 golden pack 校验结果，便于 CLI、测试和后续 CI 复用。
    入参：ok（bool）：是否通过；pack_id（str）：pack ID，解析失败时为空；
        scene_count/interaction_count/quest_count/trigger_count（int）：基础规模计数；
        branch_groups（list[A3BranchGroupSummary]）：已识别分支组；
        diagnostics（list[str]）：失败原因或警告。
    出参：A3VerificationResult。
    异常：dataclass 构造不抛业务异常。
    """

    ok: bool
    pack_id: str
    scene_count: int
    interaction_count: int
    quest_count: int
    trigger_count: int
    branch_groups: list[A3BranchGroupSummary]
    diagnostics: list[str]


@dataclass(frozen=True)
class _A3BranchValidationContext:
    """
    功能：承载 A3 分支组校验过程共享的触发器索引与诊断器。
    入参：trigger_targets（dict）：update_quest 目标索引；triggers（dict）：触发器索引；
        diagnostics（list[str]）：诊断累积器。
    出参：_A3BranchValidationContext。
    异常：dataclass 构造不做业务校验，调用方负责传入同一 bundle 的索引。
    """

    trigger_targets: dict[tuple[str, str], list[str]]
    triggers: dict[str, TriggerDef]
    diagnostics: list[str]


def verify_a3_branching_pack(pack_path: str | Path) -> A3VerificationResult:
    """
    功能：校验一个 Story Pack 是否满足 A3 golden 分支任务的最低验收门槛。
    入参：pack_path（str | Path）：待校验 pack 根目录，必须能通过通用 Story Pack 校验。
    出参：A3VerificationResult，ok 为 False 时 diagnostics 给出最小修复方向。
    异常：不向外抛 StoryPackValidationError；通用校验失败会转换为失败结果。
    """
    try:
        bundle = validate_story_pack(pack_path)
    except StoryPackValidationError as error:
        return A3VerificationResult(
            ok=False,
            pack_id="",
            scene_count=0,
            interaction_count=0,
            quest_count=0,
            trigger_count=0,
            branch_groups=[],
            diagnostics=list(error.diagnostics),
        )

    diagnostics: list[str] = []
    summary = bundle.summary
    _validate_minimum_content_shape(bundle, diagnostics)
    _validate_action_trigger_reachability(bundle, diagnostics)
    branch_groups = _collect_branch_groups(bundle, diagnostics)

    return A3VerificationResult(
        ok=not diagnostics,
        pack_id=summary.pack_id,
        scene_count=summary.scene_count,
        interaction_count=summary.interaction_count,
        quest_count=summary.quest_count,
        trigger_count=summary.trigger_count,
        branch_groups=branch_groups,
        diagnostics=diagnostics,
    )


def _validate_minimum_content_shape(
    bundle: StoryPackBundle,
    diagnostics: list[str],
) -> None:
    """
    功能：校验 A3 golden pack 的基础内容规模，避免空壳包误通过分支校验。
    入参：bundle（StoryPackBundle）：已通过通用校验的 pack；diagnostics（list[str]）：诊断累积器。
    出参：None。
    异常：不抛异常；未达标项追加 diagnostics。
    """
    summary = bundle.summary
    if summary.scene_count < 3:
        diagnostics.append("A3 golden pack 至少需要 3 个场景")
    if summary.interaction_count < 5:
        diagnostics.append("A3 golden pack 至少需要 5 个可触发交互")
    if summary.quest_count < 1:
        diagnostics.append("A3 golden pack 至少需要 1 条任务")
    if summary.trigger_count < 5:
        diagnostics.append("A3 golden pack 至少需要 5 个触发器")
    if not any(len(quest.stages) >= 4 for quest in bundle.quests.values()):
        diagnostics.append("A3 golden pack 至少需要 1 条包含 4 个以上阶段的任务")


def _validate_action_trigger_reachability(
    bundle: StoryPackBundle,
    diagnostics: list[str],
) -> None:
    """
    功能：校验 observe/talk/inspect 触发器能从声明场景和交互入口触达。
    入参：bundle（StoryPackBundle）：已通过通用校验的 pack；diagnostics（list[str]）：诊断累积器。
    出参：None。
    异常：不抛异常；缺失 scene_id 或 interaction_id 时追加 diagnostics。
    """
    scene_interactions = _scene_interaction_ids(bundle.scenes)
    for trigger in bundle.triggers.values():
        if trigger.type not in {"observe", "talk", "inspect"}:
            continue
        scene_id = _string_condition(trigger, "scene_id")
        interaction_id = _string_condition(trigger, "interaction_id")
        if not scene_id or not interaction_id:
            diagnostics.append(f"触发器 {trigger.trigger_id} 缺少 scene_id 或 interaction_id")
            continue
        if scene_id not in scene_interactions:
            diagnostics.append(f"触发器 {trigger.trigger_id} 引用不存在场景: {scene_id}")
            continue
        if interaction_id not in scene_interactions[scene_id]:
            diagnostics.append(
                f"触发器 {trigger.trigger_id} 引用场景 {scene_id} 中不存在的交互: {interaction_id}"
            )


def _collect_branch_groups(
    bundle: StoryPackBundle,
    diagnostics: list[str],
) -> list[A3BranchGroupSummary]:
    """
    功能：识别并校验 quest stage 上声明的 A3 分支组。
    入参：bundle（StoryPackBundle）：已通过通用校验的 pack；diagnostics（list[str]）：诊断累积器。
    出参：list[A3BranchGroupSummary]，仅返回结构完整的分支组摘要。
    异常：不抛异常；缺触发器、缺汇合点或分支声明冲突时追加 diagnostics。
    """
    trigger_targets = _quest_update_trigger_targets(bundle.triggers)
    context = _A3BranchValidationContext(
        trigger_targets=trigger_targets,
        triggers=bundle.triggers,
        diagnostics=diagnostics,
    )
    branch_groups: list[A3BranchGroupSummary] = []
    for quest in bundle.quests.values():
        stages_by_group = _branch_stages_by_group(quest)
        for branch_group, stages in sorted(stages_by_group.items()):
            summary = _validate_single_branch_group(
                quest=quest,
                branch_group=branch_group,
                stages=stages,
                context=context,
            )
            if summary is not None:
                branch_groups.append(summary)
        if not _completed_trigger_ids_for_quest(bundle.triggers, quest.quest_id):
            diagnostics.append(f"任务 {quest.quest_id} 缺少 completed 结案触发器")
    if not branch_groups:
        diagnostics.append("A3 golden pack 至少需要声明 1 个 a3_branch_group 分支组")
    return branch_groups


def _validate_single_branch_group(
    *,
    quest: QuestDef,
    branch_group: str,
    stages: list[QuestStage],
    context: _A3BranchValidationContext,
) -> A3BranchGroupSummary | None:
    """
    功能：校验单个分支组的互斥分支、进入触发器和汇合触发器完整性。
    入参：quest（QuestDef）：所属任务；branch_group（str）：分支组 ID；
        stages（list[QuestStage]）：分支阶段；context（_A3BranchValidationContext）：共享索引与诊断器。
    出参：A3BranchGroupSummary | None，结构完整时返回摘要，否则返回 None。
    异常：不抛异常；所有失败通过 diagnostics 反馈。
    """
    stage_ids = {stage.stage_id for stage in quest.stages}
    if len(stages) < 2:
        context.diagnostics.append(
            f"任务 {quest.quest_id} 分支组 {branch_group} 至少需要 2 个分支阶段"
        )
        return None

    merge_stage_ids = {
        str(stage.completion_condition.get(A3_MERGE_STAGE_KEY) or "") for stage in stages
    }
    merge_stage_ids.discard("")
    if len(merge_stage_ids) != 1:
        context.diagnostics.append(
            f"任务 {quest.quest_id} 分支组 {branch_group} 必须声明同一个汇合阶段"
        )
        return None
    merge_stage_id = next(iter(merge_stage_ids))
    if merge_stage_id not in stage_ids:
        context.diagnostics.append(
            f"任务 {quest.quest_id} 分支组 {branch_group} 汇合阶段不存在: {merge_stage_id}"
        )
        return None

    branch_values = _branch_values_for_stages(
        stages, quest.quest_id, branch_group, context.diagnostics
    )
    branch_trigger_ids: list[str] = []
    for stage in stages:
        target_key = (quest.quest_id, stage.stage_id)
        target_trigger_ids = context.trigger_targets.get(target_key, [])
        if not target_trigger_ids:
            context.diagnostics.append(
                f"任务 {quest.quest_id} 分支阶段 {stage.stage_id} 缺少 update_quest 入口触发器"
            )
            continue
        _validate_branch_trigger_metadata(
            context=context,
            trigger_ids=target_trigger_ids,
            quest_id=quest.quest_id,
            stage=stage,
            branch_group=branch_group,
        )
        branch_trigger_ids.extend(target_trigger_ids)

    merge_trigger_ids = context.trigger_targets.get((quest.quest_id, merge_stage_id), [])
    if not merge_trigger_ids:
        context.diagnostics.append(
            f"任务 {quest.quest_id} 分支组 {branch_group} "
            f"缺少进入汇合阶段 {merge_stage_id} 的触发器"
        )

    if not branch_values or len(branch_values) != len(stages):
        return None
    if not branch_trigger_ids or not merge_trigger_ids:
        return None
    return A3BranchGroupSummary(
        quest_id=quest.quest_id,
        branch_group=branch_group,
        branch_stage_ids=[stage.stage_id for stage in stages],
        branch_values=branch_values,
        merge_stage_id=merge_stage_id,
        branch_trigger_ids=sorted(set(branch_trigger_ids)),
        merge_trigger_ids=sorted(set(merge_trigger_ids)),
    )


def _branch_values_for_stages(
    stages: list[QuestStage],
    quest_id: str,
    branch_group: str,
    diagnostics: list[str],
) -> list[str]:
    """
    功能：读取并校验分支阶段的 branch_value，确保同组内唯一。
    入参：stages（list[QuestStage]）：候选分支阶段；quest_id（str）：所属任务；
        branch_group（str）：分支组 ID；diagnostics（list[str]）：诊断累积器。
    出参：list[str]，按阶段顺序返回 branch_value；缺失或重复时返回已读值并追加诊断。
    异常：不抛异常。
    """
    values: list[str] = []
    seen: set[str] = set()
    for stage in stages:
        value = str(stage.completion_condition.get(A3_BRANCH_VALUE_KEY) or "").strip()
        if not value:
            diagnostics.append(
                f"任务 {quest_id} 分支组 {branch_group} 阶段 {stage.stage_id} 缺少 branch_value"
            )
            continue
        if value in seen:
            diagnostics.append(f"任务 {quest_id} 分支组 {branch_group} branch_value 重复: {value}")
            continue
        seen.add(value)
        values.append(value)
    return values


def _validate_branch_trigger_metadata(
    *,
    context: _A3BranchValidationContext,
    trigger_ids: list[str],
    quest_id: str,
    stage: QuestStage,
    branch_group: str,
) -> None:
    """
    功能：校验进入分支阶段的触发器携带与 stage 对齐的 A3 分支元数据。
    入参：context（_A3BranchValidationContext）：共享索引与诊断器；trigger_ids（list[str]）：候选触发器；
        quest_id（str）：所属任务；stage（QuestStage）：目标分支阶段；
        branch_group（str）：分支组 ID。
    出参：None。
    异常：不抛异常；触发器缺元数据时追加 diagnostics。
    """
    expected_value = str(stage.completion_condition.get(A3_BRANCH_VALUE_KEY) or "").strip()
    for trigger_id in trigger_ids:
        trigger = context.triggers.get(trigger_id)
        if trigger is None:
            continue
        actual_group = _string_condition(trigger, A3_BRANCH_GROUP_KEY)
        actual_value = _string_condition(trigger, A3_BRANCH_VALUE_KEY)
        if actual_group != branch_group or actual_value != expected_value:
            context.diagnostics.append(
                "任务 "
                f"{quest_id} 分支阶段 {stage.stage_id} 的触发器 {trigger_id} "
                "缺少匹配的 a3_branch_group/branch_value"
            )


def _branch_stages_by_group(quest: QuestDef) -> dict[str, list[QuestStage]]:
    """
    功能：按 completion_condition.a3_branch_group 收集任务分支阶段。
    入参：quest（QuestDef）：待扫描任务。
    出参：dict[str, list[QuestStage]]，key 为分支组 ID。
    异常：不抛异常；空或非字符串分支组会被忽略。
    """
    grouped: dict[str, list[QuestStage]] = {}
    for stage in quest.stages:
        branch_group = str(stage.completion_condition.get(A3_BRANCH_GROUP_KEY) or "").strip()
        if not branch_group:
            continue
        grouped.setdefault(branch_group, []).append(stage)
    return grouped


def _quest_update_trigger_targets(
    triggers: dict[str, TriggerDef],
) -> dict[tuple[str, str], list[str]]:
    """
    功能：建立 update_quest 触发器的目标任务阶段索引。
    入参：triggers（dict[str, TriggerDef]）：触发器索引。
    出参：dict[tuple[str, str], list[str]]，key 为 (quest_id, target_stage_id)。
    异常：不抛异常；缺 quest_id 或 target_stage_id 的触发器不进入索引。
    """
    targets: dict[tuple[str, str], list[str]] = {}
    for trigger in triggers.values():
        if "update_quest" not in trigger.effects:
            continue
        quest_id = _string_condition(trigger, "quest_id")
        target_stage_id = _string_condition(trigger, "target_stage_id", "next_stage_id")
        if not quest_id or not target_stage_id:
            continue
        targets.setdefault((quest_id, target_stage_id), []).append(trigger.trigger_id)
    return targets


def _completed_trigger_ids_for_quest(
    triggers: dict[str, TriggerDef],
    quest_id: str,
) -> list[str]:
    """
    功能：查找会把指定任务置为 completed 的触发器。
    入参：triggers（dict[str, TriggerDef]）：触发器索引；quest_id（str）：任务 ID。
    出参：list[str]，completed 触发器 ID 列表。
    异常：不抛异常。
    """
    completed: list[str] = []
    for trigger in triggers.values():
        if "update_quest" not in trigger.effects:
            continue
        if _string_condition(trigger, "quest_id") != quest_id:
            continue
        next_status = _string_condition(trigger, "quest_status", "status")
        if next_status == "completed":
            completed.append(trigger.trigger_id)
    return completed


def _scene_interaction_ids(
    scenes: dict[str, StoryPackSceneDef],
) -> dict[str, set[str]]:
    """
    功能：把场景交互入口规整为 scene_id 到 interaction_id 集合的索引。
    入参：scenes（dict[str, StoryPackSceneDef]）：pack 场景索引。
    出参：dict[str, set[str]]。
    异常：不抛异常。
    """
    return {
        scene_id: {interaction.interaction_id for interaction in scene.interactables}
        for scene_id, scene in scenes.items()
    }


def _string_condition(trigger: TriggerDef, *keys: str) -> str:
    """
    功能：从触发器 conditions 中按候选 key 读取第一个非空字符串。
    入参：trigger（TriggerDef）：触发器定义；keys（str）：候选字段名。
    出参：str，未找到时返回空字符串。
    异常：不抛异常；非字符串值会被忽略。
    """
    for key in keys:
        value: Any = trigger.conditions.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def main() -> int:
    """
    功能：执行 A3 golden Story Pack 分支校验 CLI。
    入参：从命令行读取 pack_path。
    出参：int，0 表示验收通过，1 表示验收失败。
    异常：不抛业务异常；失败信息以 JSON 输出。
    """
    parser = argparse.ArgumentParser(description="Validate an A3 branching golden Story Pack.")
    parser.add_argument("pack_path", type=Path)
    args = parser.parse_args()
    result = verify_a3_branching_pack(args.pack_path)
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
