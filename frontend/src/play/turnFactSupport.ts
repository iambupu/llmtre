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
 * 功能：把后端 A3 branch_consequences 摘要压缩为最近结果徽标文案。
 * 入参：rawConsequences（unknown）：TurnResult.branch_consequences 原始值。
 * 出参：string[]，每条均来自后端结构化字段；字段缺失时用数量兜底。
 * 异常：不抛异常；非法数组项会被跳过。
 */
function resolveBranchConsequenceFacts(rawConsequences: unknown): string[] {
  if (!Array.isArray(rawConsequences) || rawConsequences.length === 0) {
    return [];
  }
  return rawConsequences
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .slice(0, 2)
    .map((item) => {
      const branchPath = textValue(item.branch_path, "").trim();
      const changes = Array.isArray(item.state_changes) ? item.state_changes.length : 0;
      if (branchPath && changes) {
        return `选择后果：${branchPath}（${changes} 项）`;
      }
      if (branchPath) {
        return `选择后果：${branchPath}`;
      }
      return `选择后果：${changes || 1} 项`;
    });
}

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
  facts.push(...resolveBranchConsequenceFacts(turnData.branch_consequences));
  if (Array.isArray(turnData.trigger_events) && turnData.trigger_events.length) {
    facts.push(`触发事件：${turnData.trigger_events.length} 项`);
  }
  const outcomeLabel = resolveTurnOutcomeLabel(turnData.outcome);
  if (outcomeLabel) {
    facts.push(`结果：${outcomeLabel}`);
  }
  return facts;
}
