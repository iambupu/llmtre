import { textValue } from "@/play/displayTextSupport";
import { isQuestCompleted } from "@/play/sceneGoalSupport";

/**
 * 功能：从后端任务快照中读取当前阶段标题。
 * 入参：quest（Record<string, unknown>）：单条任务展示对象。
 * 出参：string，当前阶段标题或降级文案。
 * 异常：不抛异常；字段缺失时按任务状态降级。
 */
export function resolveQuestStageTitle(quest: Record<string, unknown>): string {
  if (isQuestCompleted(quest)) {
    return "任务已完成";
  }
  return textValue(
    quest.stage_label ??
      quest.current_stage_label ??
      quest.current_stage_title ??
      quest.current_stage_id ??
      quest.status_label ??
      quest.status,
    "等待推进"
  );
}

/**
 * 功能：从后端任务快照中读取当前阶段说明。
 * 入参：quest（Record<string, unknown>）：单条任务展示对象。
 * 出参：string，阶段说明、任务说明或状态文案。
 * 异常：不抛异常；字段缺失时返回空字符串，调用方决定是否渲染。
 */
export function resolveQuestStageDescription(quest: Record<string, unknown>): string {
  if (isQuestCompleted(quest)) {
    const stageLabel = textValue(quest.stage_label ?? quest.current_stage_label, "");
    const stageDescription = textValue(
      quest.stage_description ?? quest.current_stage_description,
      ""
    );
    return [stageLabel ? `最终阶段：${stageLabel}` : "", stageDescription]
      .filter(Boolean)
      .join("。");
  }
  return textValue(
    quest.stage_description ?? quest.current_stage_description ?? quest.description,
    ""
  );
}
