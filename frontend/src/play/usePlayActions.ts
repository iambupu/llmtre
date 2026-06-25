import { useMutation } from "@tanstack/react-query";
import { commitSandbox, discardSandbox, previewSandboxDiff } from "@/api/sandbox";
import { getMemory, refreshMemory } from "@/api/memory";
import { resetSession } from "@/api/runtime";
import { createSession, deleteSession, getSession } from "@/api/sessions";
import { deleteStoryPack, importStoryPack } from "@/api/storyPacks";
import { createTurn, listAllSessionTurns } from "@/api/turns";
import type { useTurnStream } from "@/hooks/useTurnStream";
import type { StoryPackSummary } from "@/types";
import { resolveTurnQuickActions } from "@/play/actionSupport";
import { buildMessagesFromTurnHistory } from "@/play/conversationHistorySupport";
import type { PersistedPlayState } from "@/play/persistenceSupport";
import {
  buildOpeningMessages,
  initialMessages,
  nowClock,
  parseStoryPackImportDraft,
} from "@/play/persistenceSupport";
type PlayActionsParams = {
  playState: PersistedPlayState;
  selectedPack: StoryPackSummary | null;
  trimmedBackground: string;
  outputMode: "sync" | "stream";
  stream: ReturnType<typeof useTurnStream>;
  addLog: (message: string) => void;
  refetchStoryPacks: () => unknown;
  refetchSessions: () => unknown;
};

type SandboxMutationParams = {
  sessionId: string;
  setLastBackendPayload: (payload: unknown) => void;
  addLog: (message: string) => void;
  appendError: (err: unknown) => void;
};

/**
 * 功能：集中创建沙盒并入、回滚和差异预览 mutation，避免主 hook 继续膨胀。
 * 入参：SandboxMutationParams，包含 sessionId、后端载荷回写、日志和错误处理入口。
 * 出参：object，包含 commitMutation、discardMutation、sandboxDiffMutation。
 * 异常：接口异常由 mutation onError 转交 appendError；hook 本身不抛业务异常。
 */
function useSandboxMutations({
  sessionId,
  setLastBackendPayload,
  addLog,
  appendError,
}: SandboxMutationParams) {
  const commitMutation = useMutation({
    mutationFn: async () => commitSandbox(sessionId),
    onSuccess: (payload) => {
      setLastBackendPayload(payload);
      addLog("沙盒并入成功");
    },
    onError: (err) => appendError(err),
  });

  const discardMutation = useMutation({
    mutationFn: async () => discardSandbox(sessionId),
    onSuccess: (payload) => {
      setLastBackendPayload(payload);
      addLog("沙盒回滚成功");
    },
    onError: (err) => appendError(err),
  });

  const sandboxDiffMutation = useMutation({
    mutationFn: async () => previewSandboxDiff(sessionId),
    onSuccess: (payload) => {
      setLastBackendPayload(payload);
      addLog("沙盒差异已预览");
    },
    onError: (err) => appendError(err),
  });

  return { commitMutation, discardMutation, sandboxDiffMutation };
}

/**
 * 功能：集中管理游玩页的后端动作、mutation 成功回写和玩家回合提交。
 * 入参：PlayActionsParams，包含页面状态、输出模式、流式 hook、日志函数和剧本包刷新入口。
 * 出参：对象，包含各类 mutation、submitTurn 与 appendError。
 * 异常：接口异常在 hook 内捕获并写入消息流；mutation 自身保留 TanStack Query 的错误状态。
 */
export function usePlayActions({
  playState,
  selectedPack,
  trimmedBackground,
  outputMode,
  stream,
  addLog,
  refetchStoryPacks,
  refetchSessions,
}: PlayActionsParams) {
  const {
    sessionId,
    setSessionId,
    rememberSession,
    forgetSession,
    characterId,
    setCharacterId,
    setUserInput,
    setSessionData,
    setTurnData,
    setMemoryText,
    setMessages,
    selectedPackId,
    setSelectedPackId,
    packPreviewId,
    setPackPreviewId,
    packImportDraft,
    sessionData,
    setLastBackendPayload,
  } = playState;

  /**
   * 功能：把接口错误追加到对话流并记录调试日志。
   * 入参：err（unknown）：任意异常对象。
   * 出参：void。
   * 异常：不抛异常；异常内容统一转字符串降级展示。
   */
  function appendError(err: unknown): void {
    const text = String(err);
    setMessages((prev) => [...prev, { role: "error", text, at: nowClock() }]);
    addLog(`操作失败: ${text}`);
  }

  const createSessionMutation = useMutation({
    mutationFn: async () => {
      const commonInput = {
        character_id: characterId || undefined,
        sandbox_mode: false,
      };
      // 剧本源边界：新建会话必须绑定已有 pack，或用 background 先生成 pack 后再绑定。
      if (selectedPackId) {
        return createSession({
          ...commonInput,
          pack_id: selectedPackId,
          scenario_id: selectedPack?.scenario_id ?? "default",
        });
      }
      return createSession({
        ...commonInput,
        background: trimmedBackground,
      });
    },
    onSuccess: (payload) => {
      setSessionId(payload.session_id);
      rememberSession(payload);
      if (payload.character_id) {
        setCharacterId(payload.character_id);
      }
      setSelectedPackId(payload.pack_id ?? selectedPackId);
      setSessionData(payload);
      setTurnData(null);
      setMemoryText(payload.memory_summary ?? "");
      setMessages(buildOpeningMessages(payload));
      setLastBackendPayload(payload);
      addLog(
        payload.pack_id
          ? `已创建会话: ${payload.session_id} / pack=${payload.pack_id}`
          : `已创建会话: ${payload.session_id}`
      );
      void refetchSessions();
    },
    onError: (err) => appendError(err),
  });

  const loadSessionMutation = useMutation({
    mutationFn: async (targetSessionId?: string) => {
      const normalizedSessionId = (targetSessionId ?? sessionId).trim();
      if (!normalizedSessionId) {
        throw new Error("请先输入或选择会话 ID。");
      }
      const [payload, history] = await Promise.all([
        getSession(normalizedSessionId),
        listAllSessionTurns(normalizedSessionId),
      ]);
      return { payload, history };
    },
    onMutate: (targetSessionId?: string) => {
      const normalizedSessionId = (targetSessionId ?? sessionId).trim();
      // 会话切换边界：先清空上一个 session 的回合态和消息，避免历史接口返回前短暂串档。
      if (normalizedSessionId) {
        setSessionId(normalizedSessionId);
      }
      setTurnData(null);
      setMessages(initialMessages);
      setMemoryText("");
    },
    onSuccess: ({ payload, history }) => {
      setSessionId(payload.session_id);
      rememberSession(payload);
      setSessionData(payload);
      if (payload.character_id) {
        setCharacterId(payload.character_id);
      }
      setSelectedPackId(payload.pack_id ?? "");
      setTurnData(null);
      setMemoryText(payload.memory_summary ?? "");
      setMessages(buildMessagesFromTurnHistory(history, payload));
      setLastBackendPayload(payload);
      addLog(`已加载会话: ${payload.session_id} / 历史回合=${history.total}`);
      void refetchSessions();
    },
    onError: (err) => appendError(err),
  });

  const deleteSessionMutation = useMutation({
    mutationFn: async (targetSessionId: string) => deleteSession(targetSessionId.trim()),
    onSuccess: (payload) => {
      const deletedSessionId = payload.deleted_session_id;
      forgetSession(deletedSessionId);
      // 当前会话被删除后，清空所有依赖 session_id 的 UI 状态，避免后续回合请求打到 404。
      if (sessionId === deletedSessionId) {
        setSessionId("");
        setSessionData(null);
        setTurnData(null);
        setMemoryText("");
        setMessages(initialMessages);
      }
      setLastBackendPayload(payload);
      void refetchSessions();
      addLog(
        `会话已删除: ${deletedSessionId} / 回合=${payload.deleted_turns ?? 0} / 记忆=${
          payload.deleted_memory_items ?? 0
        }`
      );
    },
    onError: (err) => appendError(err),
  });

  const memoryMutation = useMutation({
    mutationFn: async () => getMemory(sessionId),
    onSuccess: (payload) => {
      setMemoryText(payload.summary ?? payload.text ?? "");
      addLog("记忆读取完成");
    },
    onError: (err) => appendError(err),
  });

  const refreshMemoryMutation = useMutation({
    mutationFn: async () => refreshMemory(sessionId),
    onSuccess: (payload) => {
      setMemoryText(payload.summary ?? payload.text ?? "");
      addLog("记忆刷新完成");
    },
    onError: (err) => appendError(err),
  });

  const resetMutation = useMutation({
    mutationFn: async () => resetSession(sessionId, true),
    onSuccess: (payload) => {
      setSessionData(payload);
      setTurnData(null);
      setMessages(initialMessages);
      setMemoryText("");
      setLastBackendPayload(payload);
      addLog("会话已重置");
    },
    onError: (err) => appendError(err),
  });

  const { commitMutation, discardMutation, sandboxDiffMutation } = useSandboxMutations({
    sessionId,
    setLastBackendPayload,
    addLog,
    appendError,
  });

  const importPackMutation = useMutation({
    mutationFn: async () => importStoryPack(parseStoryPackImportDraft(packImportDraft)),
    onSuccess: (payload) => {
      setSelectedPackId(payload.summary.pack_id);
      setPackPreviewId(payload.summary.pack_id);
      setLastBackendPayload(payload);
      void refetchStoryPacks();
      addLog(`剧本包已导入: ${payload.summary.pack_id}`);
    },
    onError: (err) => appendError(err),
  });

  const deletePackMutation = useMutation({
    mutationFn: async (packId: string) => deleteStoryPack(packId),
    onSuccess: (payload) => {
      if (selectedPackId === payload.deleted_pack_id) {
        setSelectedPackId("");
      }
      if (packPreviewId === payload.deleted_pack_id) {
        setPackPreviewId("");
      }
      setLastBackendPayload(payload);
      void refetchStoryPacks();
      addLog(`剧本包已删除: ${payload.deleted_pack_id}`);
    },
    onError: (err) => appendError(err),
  });

  /**
   * 功能：提交玩家行动，按当前输出模式选择普通回合或 SSE 流式回合。
   * 入参：text（string）：玩家输入或快捷行动文本。
   * 出参：Promise<void>。
   * 异常：接口异常会被捕获并写入 UI；取消流式输出由 hook 处理 busy 状态。
   */
  async function submitTurn(text: string): Promise<void> {
    if (!sessionId) {
      appendError("请先创建或加载会话。");
      return;
    }
    const finalText = text.trim();
    if (!finalText) {
      return;
    }
    setUserInput("");
    setMessages((prev) => [...prev, { role: "player", text: finalText, at: nowClock() }]);
    addLog(`提交回合: ${outputMode}`);
    try {
      const result =
        outputMode === "stream"
          ? await stream.run(sessionId, {
              user_input: finalText,
              character_id: characterId || undefined,
              sandbox_mode: false,
            })
          : await createTurn(sessionId, {
              user_input: finalText,
              character_id: characterId || undefined,
              sandbox_mode: false,
            });
      setTurnData(result);
      const turnQuickActions = resolveTurnQuickActions(result);
      setLastBackendPayload(result);
      setMessages((prev) => [
        ...prev,
        {
          role: "gm",
          text: result.final_response,
          at: nowClock(),
          quickActions: turnQuickActions,
        },
      ]);
      if (result.memory_summary) {
        setMemoryText(result.memory_summary);
      }
      setSessionData((prev) =>
        prev
          ? {
              ...prev,
              current_session_turn_id: result.session_turn_id,
              scene_snapshot: result.scene_snapshot ?? prev.scene_snapshot,
              active_character: result.active_character ?? prev.active_character,
              memory_summary: result.memory_summary ?? prev.memory_summary,
            }
          : prev
      );
      rememberSession({
        ...(sessionData ?? {
          session_id: result.session_id,
          current_session_turn_id: result.session_turn_id,
        }),
        session_id: result.session_id,
        current_session_turn_id: result.session_turn_id,
        scene_snapshot: result.scene_snapshot ?? sessionData?.scene_snapshot,
        active_character: result.active_character ?? sessionData?.active_character,
        memory_summary: result.memory_summary ?? sessionData?.memory_summary,
      });
      void refetchSessions();
      addLog(`回合完成: s_turn=${result.session_turn_id}, r_turn=${result.runtime_turn_id}`);
    } catch (err) {
      appendError(err);
    }
  }

  return {
    createSessionMutation,
    loadSessionMutation,
    deleteSessionMutation,
    memoryMutation,
    refreshMemoryMutation,
    resetMutation,
    commitMutation,
    discardMutation,
    sandboxDiffMutation,
    importPackMutation,
    deletePackMutation,
    appendError,
    submitTurn,
  };
}
