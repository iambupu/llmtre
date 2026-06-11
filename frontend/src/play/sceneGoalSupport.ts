import type { SceneSnapshot, StoryPackSummary, TurnResult } from "@/types";
import { textValue } from "@/play/displayTextSupport";

/**
 * 功能：判断任务快照是否已完成。
 * 入参：quest（Record<string, unknown> | undefined）：后端 scene_snapshot.active_quests 条目。
 * 出参：boolean，status 为 completed 或状态标签为“已完成”时返回 true。
 * 异常：不抛异常；缺失状态字段按未完成处理。
 */
export function isQuestCompleted(quest: Record<string, unknown> | undefined): boolean {
  if (!quest) {
    return false;
  }
  const status = textValue(quest.status, "").trim().toLowerCase();
  const statusLabel = textValue(quest.status_label, "").trim();
  return status === "completed" || statusLabel === "已完成";
}

/**
 * 功能：从回合结果与场景提示中解析“当前状态”文案，避免把内部 outcome 代码直接显示给玩家。
 * 入参：turnData（TurnResult | null）：最近回合结果；scene（SceneSnapshot | null）：当前场景快照。
 * 出参：string，优先展示自然语言状态，缺失时返回默认占位文案。
 * 异常：不抛异常；字段缺失时按优先级降级到默认提示。
 */
export function resolveSceneStatus(
  turnData: TurnResult | null,
  scene: SceneSnapshot | null
): string {
  return textValue(
    turnData?.suggested_next_step ??
      scene?.ui_hints?.status_text ??
      scene?.ui_hints?.status ??
      scene?.ui_hints?.hint,
    "等待玩家输入明确行动。"
  );
}

/**
 * 功能：从任务快照或剧本摘要中生成玩家可读的当前目标，不写入或推断任务状态。
 * 入参：quests（Record<string, unknown>[]）：后端 scene_snapshot.active_quests；
 *   selectedPack（StoryPackSummary | null）：当前选中剧本包摘要。
 * 出参：string，适合展示在玩家主界面的目标文案。
 * 异常：不抛异常；字段缺失时降级为通用探索目标。
 */
export function resolvePlayerGoal(
  quests: Record<string, unknown>[],
  selectedPack: StoryPackSummary | null
): string {
  const firstQuest = quests[0];
  if (firstQuest) {
    if (isQuestCompleted(firstQuest)) {
      return `已完成：${textValue(
        firstQuest.name ?? firstQuest.title ?? firstQuest.quest_id,
        "当前任务"
      )}`;
    }
    return textValue(
      firstQuest.stage_label ??
        firstQuest.current_stage_label ??
        firstQuest.current_stage_title ??
        firstQuest.stage_description ??
        firstQuest.name ??
        firstQuest.title ??
        firstQuest.description ??
        firstQuest.quest_id,
      "推进当前任务"
    );
  }
  if (selectedPack?.quest_count) {
    return `探索 ${selectedPack.title}，寻找可推进的任务线索。`;
  }
  return "探索当前场景，选择一个可用行动。";
}
