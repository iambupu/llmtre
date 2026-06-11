import type { SessionPayload, TurnListPayload, TurnSummary } from "@/types";
import { textValue } from "@/play/displayTextSupport";
import { isRenderableQuickAction } from "@/play/quickActionLayout";
import {
  buildOpeningMessages,
  nowClock,
  type ChatMessage,
} from "@/play/persistenceSupport";

/**
 * 功能：把后端 ISO 时间粗略压成聊天气泡使用的时分秒。
 * 入参：createdAt（string | undefined）：后端回合创建时间，通常为 ISO8601。
 * 出参：string，优先返回服务端时间中的 HH:mm:ss，缺失时使用当前本地时间兜底。
 * 异常：不抛异常；非法时间字符串会按当前本地时间降级，避免坏历史阻断会话加载。
 */
export function historyMessageClock(createdAt?: string): string {
  const text = textValue(createdAt, "");
  const match = text.match(/T(\d{2}:\d{2}:\d{2})/);
  return match?.[1] ?? nowClock();
}

/**
 * 功能：从会话详情里提取可展示的当前快捷动作，挂到恢复后的最后一条 GM 消息。
 * 入参：sessionPayload（SessionPayload | null | undefined）：加载会话接口返回的当前状态。
 * 出参：string[]，已过滤 move/inspect 等裸内部动作 key 的玩家可读动作。
 * 异常：不抛异常；缺失或非字符串动作会被过滤。
 */
export function sessionResumeQuickActions(
  sessionPayload?: SessionPayload | null
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const action of sessionPayload?.quick_actions ?? []) {
    const actionText = textValue(action, "").trim();
    if (!isRenderableQuickAction(actionText) || seen.has(actionText)) {
      continue;
    }
    seen.add(actionText);
    result.push(actionText);
  }
  return result.slice(0, 10);
}

/**
 * 功能：把单条后端回合摘要转换为玩家消息与 GM 消息。
 * 入参：turn（TurnSummary）：后端持久化的会话内回合摘要。
 * 出参：ChatMessage[]，按玩家输入在前、GM 回应在后的顺序返回。
 * 异常：不抛异常；空白输入或空白回应会被跳过，避免渲染无意义气泡。
 */
export function turnSummaryToMessages(turn: TurnSummary): ChatMessage[] {
  const at = historyMessageClock(turn.created_at);
  const userInput = textValue(turn.user_input, "").trim();
  const finalResponse = textValue(turn.final_response, "").trim();
  const messages: ChatMessage[] = [];
  if (userInput) {
    messages.push({ role: "player", text: userInput, at });
  }
  if (finalResponse) {
    messages.push({ role: "gm", text: finalResponse, at });
  }
  return messages;
}

/**
 * 功能：把会话历史分页结果恢复成聊天面板消息，并在空历史时回退到开场叙事。
 * 入参：history（TurnListPayload）：后端按 session_id 返回的持久化历史；
 *   sessionPayload（SessionPayload | null | undefined）：当前会话详情，用于空历史开场和快捷动作。
 * 出参：ChatMessage[]，只包含该 session 的历史消息。
 * 异常：不抛异常；历史顺序异常时按 session_turn_id 重新排序，字段缺失时降级为空消息。
 */
export function buildMessagesFromTurnHistory(
  history: TurnListPayload,
  sessionPayload?: SessionPayload | null
): ChatMessage[] {
  const turns = [...history.items].sort(
    (left, right) => left.session_turn_id - right.session_turn_id
  );
  const messages = turns.flatMap((turn) => turnSummaryToMessages(turn));
  if (!messages.length && sessionPayload) {
    return buildOpeningMessages(sessionPayload);
  }

  const quickActions = sessionResumeQuickActions(sessionPayload);
  if (!quickActions.length) {
    return messages;
  }

  // 恢复边界：历史接口只保存文本，当前可继续动作来自 session 详情的最新场景状态。
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role !== "gm") {
      continue;
    }
    messages[index] = { ...messages[index], quickActions };
    break;
  }
  return messages;
}
