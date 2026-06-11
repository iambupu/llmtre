import type { Dispatch, SetStateAction } from "react";
import type { SessionPayload, SessionSummary, StoryPackImportPayload, TurnResult } from "@/types";

import { textValue } from "@/play/displayTextSupport";

export type ChatMessage = {
  role: "system" | "player" | "gm" | "error";
  text: string;
  at: string;
  quickActions?: string[];
};


export const initialMessages: ChatMessage[] = [];
export const PLAY_SESSION_STORAGE_KEY = "llmtre.app.play-session";
export const MAX_RECENT_SESSION_IDS = 8;

/**
 * 功能：生成 A2-Release 导入面板的初始 Story Pack JSON 草稿。
 * 入参：无。
 * 出参：string，默认返回空字符串，避免在系统 UI 中内置固定游戏内容。
 * 异常：不抛异常；外部剧本内容必须由用户粘贴、上传或通过后端生成。
 */
export function defaultStoryPackImportDraft(): string {
  return "";
}

export type PersistedAppState = {
  sessionId: string;
  recentSessionIds: string[];
  recentSessions: SessionSummary[];
  characterId: string;
  userInput: string;
  background: string;
  sessionData: SessionPayload | null;
  turnData: TurnResult | null;
  memoryText: string;
  messages: ChatMessage[];
  selectedPackId: string;
};

export type PersistedPlayState = {
  sessionId: string;
  setSessionId: Dispatch<SetStateAction<string>>;
  recentSessionIds: string[];
  recentSessions: SessionSummary[];
  rememberSession: (value: SessionPayload | SessionSummary | string) => void;
  forgetSession: (sessionId: string) => void;
  characterId: string;
  setCharacterId: Dispatch<SetStateAction<string>>;
  userInput: string;
  setUserInput: Dispatch<SetStateAction<string>>;
  sessionData: SessionPayload | null;
  setSessionData: Dispatch<SetStateAction<SessionPayload | null>>;
  turnData: TurnResult | null;
  setTurnData: Dispatch<SetStateAction<TurnResult | null>>;
  memoryText: string;
  setMemoryText: Dispatch<SetStateAction<string>>;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  selectedPackId: string;
  setSelectedPackId: Dispatch<SetStateAction<string>>;
  background: string;
  setBackground: Dispatch<SetStateAction<string>>;
  packPreviewId: string;
  setPackPreviewId: Dispatch<SetStateAction<string>>;
  packImportDraft: string;
  setPackImportDraft: Dispatch<SetStateAction<string>>;
  lastBackendPayload: unknown;
  setLastBackendPayload: Dispatch<SetStateAction<unknown>>;
  didHydrate: boolean;
  hydratedSessionId: string;
};

/**
 * 功能：清洗并截断本地保存的最近会话 ID 列表，保证 UI 下拉框只展示有效且去重的会话。
 * 入参：values（unknown）：可能来自 sessionStorage 的任意值；limit（number，默认 MAX_RECENT_SESSION_IDS）：最大保留数量。
 * 出参：string[]，按最近使用顺序排列的会话 ID。
 * 异常：不抛异常；非法输入按空列表降级。
 */
export function normalizeRecentSessionIds(
  values: unknown,
  limit = MAX_RECENT_SESSION_IDS
): string[] {
  if (!Array.isArray(values)) {
    return [];
  }
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    if (typeof value !== "string") {
      continue;
    }
    const sessionId = value.trim();
    if (!sessionId || seen.has(sessionId)) {
      continue;
    }
    seen.add(sessionId);
    result.push(sessionId);
    if (result.length >= limit) {
      break;
    }
  }
  return result;
}

/**
 * 功能：把一个会话 ID 提升到最近会话列表首位，用于成功创建或加载后的本地选择入口。
 * 入参：current（string[]）：当前最近会话列表；sessionId（string）：待记录会话 ID。
 * 出参：string[]，去重并限制长度后的新列表。
 * 异常：不抛异常；空白 sessionId 会返回规范化后的原列表。
 */
export function rememberRecentSessionId(current: string[], sessionId: string): string[] {
  const normalizedSessionId = sessionId.trim();
  if (!normalizedSessionId) {
    return normalizeRecentSessionIds(current);
  }
  return normalizeRecentSessionIds([normalizedSessionId, ...current]);
}

/**
 * 功能：把后端会话详情或列表项规范化为本地可长期缓存的会话摘要。
 * 入参：value（unknown）：可能是 SessionPayload、SessionSummary 或历史缓存对象。
 * 出参：SessionSummary | null，合法会话返回摘要，缺少 session_id 时返回 null。
 * 异常：不抛异常；非法字段按空值或 0 回合降级，避免坏缓存阻断页面水合。
 */
export function normalizeSessionSummary(value: unknown): SessionSummary | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const source = value as Record<string, unknown>;
  const sessionId = textValue(source.session_id, "");
  if (!sessionId) {
    return null;
  }
  const location =
    source.scene_snapshot && typeof source.scene_snapshot === "object"
      ? (source.scene_snapshot as Record<string, unknown>).current_location
      : null;
  const locationMap =
    location && typeof location === "object" && !Array.isArray(location)
      ? (location as Record<string, unknown>)
      : null;
  const currentTurn = Number(source.current_session_turn_id ?? 0);
  return {
    session_id: sessionId,
    character_id: textValue(source.character_id, "") || undefined,
    base_character_id: textValue(source.base_character_id, "") || undefined,
    runtime_character_id: textValue(source.runtime_character_id, "") || undefined,
    sandbox_mode: typeof source.sandbox_mode === "boolean" ? source.sandbox_mode : undefined,
    current_session_turn_id: Number.isFinite(currentTurn) ? Math.max(0, currentTurn) : 0,
    memory_summary: textValue(source.memory_summary, "") || undefined,
    pack_id: textValue(source.pack_id, "") || null,
    pack_title: textValue(source.pack_title, "") || null,
    scenario_id: textValue(source.scenario_id, "") || null,
    pack_version: textValue(source.pack_version, "") || null,
    compiled_artifact_hash: textValue(source.compiled_artifact_hash, "") || null,
    current_scene_id:
      textValue(source.current_scene_id, "") ||
      textValue(locationMap?.id ?? locationMap?.scene_id ?? locationMap?.location_id, "") ||
      null,
    current_scene_title:
      textValue(source.current_scene_title, "") ||
      textValue(locationMap?.name ?? locationMap?.label, "") ||
      null,
    created_at: textValue(source.created_at, "") || undefined,
    last_active_at: textValue(source.last_active_at, "") || undefined,
    updated_at: textValue(source.updated_at, "") || undefined,
  };
}

/**
 * 功能：规范化本地缓存的最近会话摘要，并兼容旧版只保存 ID 的缓存结构。
 * 入参：values（unknown）：历史 recentSessions；fallbackIds（unknown）：旧版 recentSessionIds。
 * 出参：SessionSummary[]，按最近优先、去重且限制数量排列。
 * 异常：不抛异常；坏缓存条目会被丢弃，裸 ID 会降级为 0 回合摘要。
 */
export function normalizeRecentSessions(
  values: unknown,
  fallbackIds: unknown = [],
): SessionSummary[] {
  const result: SessionSummary[] = [];
  const seen = new Set<string>();
  const push = (summary: SessionSummary | null) => {
    if (!summary || seen.has(summary.session_id)) {
      return;
    }
    seen.add(summary.session_id);
    result.push(summary);
  };
  if (Array.isArray(values)) {
    for (const value of values) {
      push(normalizeSessionSummary(value));
      if (result.length >= MAX_RECENT_SESSION_IDS) {
        return result;
      }
    }
  }
  for (const sessionId of normalizeRecentSessionIds(fallbackIds)) {
    push(
      normalizeSessionSummary({
        session_id: sessionId,
        current_session_turn_id: 0,
      })
    );
    if (result.length >= MAX_RECENT_SESSION_IDS) {
      break;
    }
  }
  return result;
}

/**
 * 功能：把会话详情或裸 ID 提升到最近会话摘要首位，供本地长期选择列表兜底。
 * 入参：current（SessionSummary[]）：当前本地最近摘要；value（SessionPayload | SessionSummary | string）：待记录会话。
 * 出参：SessionSummary[]，去重并限制长度后的新列表。
 * 异常：不抛异常；空白或非法会话会返回规范化后的原列表。
 */
export function rememberRecentSession(
  current: SessionSummary[],
  value: SessionPayload | SessionSummary | string,
): SessionSummary[] {
  const summary =
    typeof value === "string"
      ? normalizeSessionSummary({ session_id: value, current_session_turn_id: 0 })
      : normalizeSessionSummary(value);
  if (!summary) {
    return normalizeRecentSessions(current);
  }
  return normalizeRecentSessions([summary, ...current]);
}

/**
 * 功能：从本地最近会话摘要中移除指定会话，避免已删除进度继续出现在选择器里。
 * 入参：current（SessionSummary[]）：当前本地最近摘要；sessionId（string）：待移除会话 ID。
 * 出参：SessionSummary[]，移除目标并重新规范化后的列表。
 * 异常：不抛异常；空白 sessionId 会返回规范化后的原列表。
 */
export function forgetRecentSession(
  current: SessionSummary[],
  sessionId: string,
): SessionSummary[] {
  const normalizedSessionId = sessionId.trim();
  if (!normalizedSessionId) {
    return normalizeRecentSessions(current);
  }
  return normalizeRecentSessions(
    current.filter((item) => item.session_id !== normalizedSessionId)
  );
}

/**
 * 功能：合并后端真实会话列表与本地最近缓存，后端数据优先保留最新进度字段。
 * 入参：serverItems（SessionSummary[]）：后端列表；localItems（SessionSummary[]）：本地缓存。
 * 出参：SessionSummary[]，去重后的展示列表。
 * 异常：不抛异常；非法项会被 normalizeSessionSummary 过滤。
 */
export function mergeSessionChoices(
  serverItems: SessionSummary[],
  localItems: SessionSummary[],
): SessionSummary[] {
  const result: SessionSummary[] = [];
  const seen = new Set<string>();
  for (const item of [...serverItems, ...localItems]) {
    const summary = normalizeSessionSummary(item);
    if (!summary || seen.has(summary.session_id)) {
      continue;
    }
    seen.add(summary.session_id);
    result.push(summary);
  }
  return result;
}

/**
 * 功能：生成聊天消息使用的本地时间戳。
 * 入参：无。
 * 出参：string，24 小时制中文本地时间。
 * 异常：不抛异常；浏览器 Intl 不可用时由运行时 Date 默认能力处理。
 */
export function nowClock(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * 功能：把未知值安全转换为字符串，供 UI 展示后端契约里的可选字段。
 * 入参：value（unknown）：任意后端返回值；fallback（string，默认 '-'）：空值兜底文案。
 * 出参：string，适合直接渲染的文本。
 * 异常：不抛异常；无法识别的对象会降级为 fallback，避免界面渲染失败。
 */

/**
 * 功能：把导入面板里的 JSON 草稿解析为 StoryPackImportPayload。
 * 入参：draft（string）：用户编辑的 JSON 字符串。
 * 出参：StoryPackImportPayload，顶层必须为对象。
 * 异常：JSON 非法或顶层不是对象时抛出 Error，由 mutation 错误处理写入 UI。
 */
export function parseStoryPackImportDraft(draft: string): StoryPackImportPayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(draft);
  } catch (error) {
    throw new Error(`导入 JSON 解析失败: ${String(error)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("导入 JSON 顶层必须是对象");
  }
  return parsed as StoryPackImportPayload;
}


/**
 * 功能：根据建会话后的场景快照构造首条开局叙事消息，避免向玩家暴露系统提示文案。
 * 入参：payload（SessionPayload）：创建会话接口返回的完整会话数据。
 * 出参：ChatMessage[]，有可用场景信息时返回一条 GM 文案，否则返回空数组。
 * 异常：不抛异常；字段缺失时降级为不注入开局消息，仅保留调试日志。
 */
export function buildOpeningMessages(payload: SessionPayload): ChatMessage[] {
  const location = payload.scene_snapshot?.current_location;
  const title = textValue(location?.name ?? location?.label, "");
  const description = textValue(
    location?.description ?? payload.scene_snapshot?.ui_hints?.description,
    ""
  );
  const parts = [title, description].filter(Boolean);
  if (!parts.length) {
    return [];
  }
  return [{ role: "gm", text: parts.join("："), at: nowClock() }];
}
