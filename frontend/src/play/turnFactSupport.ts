import type { TurnResult } from "@/types";
import { textValue } from "@/play/displayTextSupport";

const TURN_OUTCOME_LABELS: Record<string, string> = {
  valid_action: "行动成立",
  invalid_action: "行动未成立",
  clarification_needed: "需要补充说明",
  no_action: "未采取行动",
  error: "执行异常",
};

/**
 * 功能：把后端 outcome 枚举转换为玩家可读结果标签。
 * 入参：outcome（string | undefined）：后端回合结果枚举。
 * 出参：string，已知枚举返回中文标签，未知枚举按“系统结果”兜底。
 * 异常：不抛异常；空白输入返回空字符串，避免无结果时展示噪声。
 */
export function resolveTurnOutcomeLabel(outcome?: string): string {
  const normalized = textValue(outcome, "").trim().toLowerCase();
  if (!normalized) {
    return "";
  }
  return TURN_OUTCOME_LABELS[normalized] ?? "系统结果";
}

/**
 * 功能：提取最近回合里的状态、任务和触发器摘要，供玩家判断行动后果。
 * 入参：turnData（TurnResult | null）：最近回合结果。
 * 出参：string[]，只展示后端已返回的事实或调试数量，不伪造状态变化。
 * 异常：不抛异常；字段缺失时返回空数组。
 */
export function resolveTurnFacts(turnData: TurnResult | null): string[] {
  if (!turnData) {
    return [];
  }
  const facts: string[] = [];
  const statusSummary = textValue(turnData.active_character?.status_summary, "");
  if (statusSummary) {
    facts.push(`状态：${statusSummary}`);
  }
  if (Array.isArray(turnData.quest_updates) && turnData.quest_updates.length) {
    facts.push(`任务更新：${turnData.quest_updates.length} 项`);
  }
  if (Array.isArray(turnData.trigger_events) && turnData.trigger_events.length) {
    facts.push(`触发事件：${turnData.trigger_events.length} 项`);
  }
  const outcomeLabel = resolveTurnOutcomeLabel(turnData.outcome);
  if (outcomeLabel) {
    facts.push(`结果：${outcomeLabel}`);
  }
  return facts;
}
