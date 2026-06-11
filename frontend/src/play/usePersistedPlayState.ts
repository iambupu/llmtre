import { useEffect, useState } from "react";
import type { SessionPayload, SessionSummary, TurnResult } from "@/types";
import type { ChatMessage, PersistedAppState, PersistedPlayState } from "@/play/persistenceSupport";
import {
  PLAY_SESSION_STORAGE_KEY,
  defaultStoryPackImportDraft,
  forgetRecentSession,
  initialMessages,
  normalizeRecentSessions,
  normalizeRecentSessionIds,
  rememberRecentSession,
} from "@/play/persistenceSupport";
/**
 * 功能：集中管理可跨刷新恢复的游玩页状态，并同步到 localStorage。
 * 入参：无。
 * 出参：PersistedPlayState，包含状态值与对应 setter，供 App 编排 API 与布局。
 * 异常：水合 JSON 解析失败时内部清理损坏缓存并降级到初始状态，不向组件外抛出。
 */
export function usePersistedPlayState(): PersistedPlayState {
  const [sessionId, setSessionId] = useState("");
  const [recentSessionIds, setRecentSessionIds] = useState<string[]>([]);
  const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
  const [characterId, setCharacterId] = useState("player_01");
  const [userInput, setUserInput] = useState("");
  const [sessionData, setSessionData] = useState<SessionPayload | null>(null);
  const [turnData, setTurnData] = useState<TurnResult | null>(null);
  const [memoryText, setMemoryText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [selectedPackId, setSelectedPackId] = useState("");
  const [background, setBackground] = useState("");
  const [packPreviewId, setPackPreviewId] = useState("");
  const [packImportDraft, setPackImportDraft] = useState(defaultStoryPackImportDraft);
  const [didHydrate, setDidHydrate] = useState(false);
  const [hydratedSessionId, setHydratedSessionId] = useState("");
  const [lastBackendPayload, setLastBackendPayload] = useState<unknown>(null);

  useEffect(() => {
    try {
      const raw =
        localStorage.getItem(PLAY_SESSION_STORAGE_KEY) ??
        sessionStorage.getItem(PLAY_SESSION_STORAGE_KEY);
      if (!raw) {
        setDidHydrate(true);
        return;
      }
      const parsed = JSON.parse(raw) as Partial<PersistedAppState>;
      const savedSessionId = typeof parsed.sessionId === "string" ? parsed.sessionId : "";
      setSessionId(savedSessionId);
      setHydratedSessionId(savedSessionId);
      // 兼容旧缓存：如果历史缓存只有当前 sessionId，也把它补进最近列表。
      const hydratedRecentSessions = normalizeRecentSessions(
        parsed.recentSessions,
        [savedSessionId, ...(parsed.recentSessionIds ?? [])]
      );
      setRecentSessions(hydratedRecentSessions);
      setRecentSessionIds(
        normalizeRecentSessionIds([
          savedSessionId,
          ...hydratedRecentSessions.map((item) => item.session_id),
        ])
      );
      setCharacterId(
        typeof parsed.characterId === "string" && parsed.characterId
          ? parsed.characterId
          : "player_01"
      );
      setUserInput(typeof parsed.userInput === "string" ? parsed.userInput : "");
      setSessionData((parsed.sessionData as SessionPayload | null) ?? null);
      setTurnData((parsed.turnData as TurnResult | null) ?? null);
      setMemoryText(typeof parsed.memoryText === "string" ? parsed.memoryText : "");
      setSelectedPackId(typeof parsed.selectedPackId === "string" ? parsed.selectedPackId : "");
      setBackground(typeof parsed.background === "string" ? parsed.background : "");
      setMessages(
        Array.isArray(parsed.messages) ? (parsed.messages as ChatMessage[]) : initialMessages
      );
    } catch {
      localStorage.removeItem(PLAY_SESSION_STORAGE_KEY);
      sessionStorage.removeItem(PLAY_SESSION_STORAGE_KEY);
      setHydratedSessionId("");
    } finally {
      setDidHydrate(true);
    }
  }, []);

  useEffect(() => {
    if (!didHydrate) {
      return;
    }
    const payload: PersistedAppState = {
      sessionId,
      recentSessionIds,
      recentSessions,
      characterId,
      userInput,
      sessionData,
      turnData,
      memoryText,
      messages,
      selectedPackId,
      background,
    };
    localStorage.setItem(PLAY_SESSION_STORAGE_KEY, JSON.stringify(payload));
  }, [
    didHydrate,
    sessionId,
    recentSessionIds,
    recentSessions,
    characterId,
    userInput,
    sessionData,
    turnData,
    memoryText,
    messages,
    selectedPackId,
    background,
  ]);

  /**
   * 功能：记录最近成功创建或加载的会话摘要，供顶部下拉框长期选择。
   * 入参：value（SessionPayload | SessionSummary | string）：后端确认存在的会话或兼容裸 ID。
   * 出参：void。
   * 异常：不抛异常；空白 ID 或坏摘要会被 rememberRecentSession 过滤。
   */
  function rememberSession(value: SessionPayload | SessionSummary | string): void {
    setRecentSessions((current) => {
      const updated = rememberRecentSession(current, value);
      setRecentSessionIds(updated.map((item) => item.session_id));
      return updated;
    });
  }

  /**
   * 功能：从本地最近会话列表中移除已确认删除的会话。
   * 入参：deletedSessionId（string）：后端确认删除的会话 ID。
   * 出参：void。
   * 异常：不抛异常；空白 ID 会按无操作降级。
   */
  function forgetSession(deletedSessionId: string): void {
    setRecentSessions((current) => {
      const updated = forgetRecentSession(current, deletedSessionId);
      setRecentSessionIds(updated.map((item) => item.session_id));
      return updated;
    });
  }

  return {
    sessionId,
    setSessionId,
    recentSessionIds,
    recentSessions,
    rememberSession,
    forgetSession,
    characterId,
    setCharacterId,
    userInput,
    setUserInput,
    sessionData,
    setSessionData,
    turnData,
    setTurnData,
    memoryText,
    setMemoryText,
    messages,
    setMessages,
    selectedPackId,
    setSelectedPackId,
    background,
    setBackground,
    packPreviewId,
    setPackPreviewId,
    packImportDraft,
    setPackImportDraft,
    lastBackendPayload,
    setLastBackendPayload,
    didHydrate,
    hydratedSessionId,
  };
}
