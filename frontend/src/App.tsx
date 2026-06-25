import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { listSessions } from "@/api/sessions";
import { getStoryPack, listStoryPacks } from "@/api/storyPacks";
import { useTurnStream } from "@/hooks/useTurnStream";
import { useDebugStore } from "@/stores/debugStore";
import { useStreamStore } from "@/stores/streamStore";
import { useUiStore } from "@/stores/uiStore";
import type { ActiveCharacter } from "@/types";
import { PlayHeader } from "@/play/PlayHeader";
import { MobileDebugLauncher } from "@/play/DebugPanel";
import type { DebugPanelData } from "@/play/DebugPanel";
import { PlayContent } from "@/play/PlayContent";
import { usePersistedPlayState } from "@/play/usePersistedPlayState";
import { usePlayActions } from "@/play/usePlayActions";
import { resolveSceneQuickActionLayout } from "@/play/actionSupport";
import { textValue } from "@/play/displayTextSupport";
import {
  buildSceneDisplayLabelResolver,
  parseCharacterMetrics,
  resolveInventoryItems,
  resolvePlayerGoal,
} from "@/play/sceneSupport";
import { mergeSessionChoices } from "@/play/persistenceSupport";
/**
 * 功能：A2 React 可玩页面，复用后端剧本会话 API 并以 shadcn/ui 组件组织游戏交互。
 * 入参：无。
 * 出参：JSX.Element。
 * 异常：组件内部捕获接口错误并写入消息流与调试日志，不让异常中断页面。
 */
export function App() {
  const playState = usePersistedPlayState();
  const autoLoadedSessionRef = useRef("");
  const {
    sessionId,
    setSessionId,
    recentSessions,
    characterId,
    setCharacterId,
    userInput,
    setUserInput,
    sessionData,
    turnData,
    memoryText,
    messages,
    selectedPackId,
    setSelectedPackId,
    background,
    setBackground,
    packPreviewId,
    setPackPreviewId,
    packImportDraft,
    setPackImportDraft,
    lastBackendPayload,
  } = playState;

  const outputMode = useUiStore((s) => s.outputMode);
  const setOutputMode = useUiStore((s) => s.setOutputMode);
  const debugVisible = useUiStore((s) => s.debugVisible);
  const toggleDebug = useUiStore((s) => s.toggleDebug);
  const isBusy = useStreamStore((s) => s.isBusy);
  const streamingText = useStreamStore((s) => s.streamingText);
  const addLog = useDebugStore((s) => s.addLog);
  const lastRequest = useDebugStore((s) => s.lastRequest);
  const lastSseEvent = useDebugStore((s) => s.lastSseEvent);
  const logs = useDebugStore((s) => s.logs);

  const stream = useTurnStream();
  const storyPacksQuery = useQuery({
    queryKey: ["story-packs"],
    queryFn: listStoryPacks,
    staleTime: 30_000,
  });
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => listSessions(20),
    staleTime: 10_000,
  });
  const packDetailQuery = useQuery({
    queryKey: ["story-pack", packPreviewId],
    queryFn: () => getStoryPack(packPreviewId),
    enabled: Boolean(packPreviewId),
    staleTime: 30_000,
  });
  const scene = turnData?.scene_snapshot ?? sessionData?.scene_snapshot ?? null;
  const activeCharacter: ActiveCharacter | null =
    turnData?.active_character ?? sessionData?.active_character ?? null;
  const sessionCharacterId = textValue(
    sessionData?.character_id ?? activeCharacter?.id ?? activeCharacter?.character_id,
    ""
  );
  const hasSession = Boolean(sessionData?.session_id);
  const metrics = parseCharacterMetrics(activeCharacter);
  const inventory = useMemo(
    () => resolveInventoryItems(activeCharacter),
    [activeCharacter]
  );
  const quests = scene?.active_quests ?? [];
  const sceneQuickActionLayout = useMemo(
    () => resolveSceneQuickActionLayout(turnData, sessionData, scene),
    [turnData, sessionData, scene]
  );
  const sceneDisplayResolver = useMemo(
    () => buildSceneDisplayLabelResolver(scene),
    [scene]
  );
  const displayedMemory =
    memoryText ||
    turnData?.memory_summary ||
    sessionData?.memory_summary ||
    scene?.recent_memory ||
    "";
  const sessionTurn =
    turnData?.session_turn_id ?? sessionData?.current_session_turn_id ?? 0;
  const storyPacks = storyPacksQuery.data?.packs ?? [];
  // 保存列表以服务端持久化结果为准；本地缓存只在列表接口加载中或失败时兜底，避免已删除会话被缓存补回菜单。
  const sessionChoices = useMemo(
    () =>
      sessionsQuery.isSuccess
        ? mergeSessionChoices(sessionsQuery.data?.items ?? [], [])
        : mergeSessionChoices([], recentSessions),
    [sessionsQuery.data?.items, sessionsQuery.isSuccess, recentSessions]
  );
  const selectedPack = storyPacks.find((pack) => pack.pack_id === selectedPackId) ?? null;
  const playerGoal = resolvePlayerGoal(quests, selectedPack);
  const storyPackDiagnostics = storyPacksQuery.data?.diagnostics ?? {};
  const trimmedBackground = background.trim();
  const canCreateSession = Boolean(selectedPackId || trimmedBackground);

  const {
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
    submitTurn,
  } = usePlayActions({
    playState,
    selectedPack,
    trimmedBackground,
    outputMode,
    stream,
    addLog,
    refetchStoryPacks: storyPacksQuery.refetch,
    refetchSessions: sessionsQuery.refetch,
  });
  const loadSavedSession = loadSessionMutation.mutate;
  useEffect(() => {
    const hydratedSessionId = playState.hydratedSessionId.trim();
    if (
      !playState.didHydrate ||
      !hydratedSessionId ||
      sessionId.trim() !== hydratedSessionId ||
      autoLoadedSessionRef.current === hydratedSessionId
    ) {
      return;
    }
    autoLoadedSessionRef.current = hydratedSessionId;
    // 恢复边界：刷新进入时以后端 session/turns 为准，覆盖 localStorage 中可能过期的消息和 turnData。
    loadSavedSession(hydratedSessionId);
  }, [
    loadSavedSession,
    playState.didHydrate,
    playState.hydratedSessionId,
    sessionId,
  ]);
  const debugPanelData: DebugPanelData = {
    lastRequest,
    lastSseEvent,
    logs,
    turnData,
    sessionData,
    memoryText: displayedMemory,
    backendPayload: lastBackendPayload,
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <PlayHeader
        characterId={characterId}
        sessionId={sessionId}
        sessionChoices={sessionChoices}
        selectedPackId={selectedPackId}
        storyPacks={storyPacks}
        background={background}
        isBusy={isBusy}
        storyPacksLoading={storyPacksQuery.isLoading}
        sessionsLoading={sessionsQuery.isLoading}
        createPending={createSessionMutation.isPending}
        sessionDeletePending={deleteSessionMutation.isPending}
        canCreateSession={canCreateSession}
        debugVisible={debugVisible}
        logsCount={logs.length}
        onCharacterIdChange={setCharacterId}
        onSessionIdChange={setSessionId}
        onSelectedPackIdChange={setSelectedPackId}
        onBackgroundChange={setBackground}
        onCreateSession={() => createSessionMutation.mutate()}
        onLoadSession={() => loadSessionMutation.mutate(sessionId)}
        onSelectSession={(selectedSessionId) => loadSessionMutation.mutate(selectedSessionId)}
        onDeleteSession={(selectedSessionId) => deleteSessionMutation.mutate(selectedSessionId)}
        onResetSession={() => resetMutation.mutate()}
        onToggleDebug={toggleDebug}
      />

      <PlayContent
        debugVisible={debugVisible}
        scene={scene}
        turnData={turnData}
        isBusy={isBusy}
        playerGoal={playerGoal}
        sceneQuickActionLayout={sceneQuickActionLayout}
        sceneDisplayResolver={sceneDisplayResolver}
        submitTurn={submitTurn}
        messages={messages}
        streamingText={streamingText}
        userInput={userInput}
        outputMode={outputMode}
        onInputChange={setUserInput}
        onModeChange={setOutputMode}
        onAbort={stream.abort}
        sessionCharacterId={sessionCharacterId}
        characterId={characterId}
        activeCharacter={activeCharacter}
        metrics={metrics}
        sessionTurn={sessionTurn}
        sandboxMode={Boolean(sessionData?.sandbox_mode)}
        hasSession={hasSession}
        inventory={inventory}
        quests={quests}
        storyPacks={storyPacks}
        storyPackDiagnostics={storyPackDiagnostics}
        selectedPack={selectedPack}
        packPreview={packDetailQuery.data ?? null}
        packImportDraft={packImportDraft}
        sessionId={sessionId}
        displayedMemory={displayedMemory}
        packPanelDisabled={isBusy}
        storyPacksLoading={storyPacksQuery.isLoading}
        packPreviewLoading={packDetailQuery.isLoading}
        packImporting={importPackMutation.isPending}
        packDeleting={deletePackMutation.isPending}
        onPreviewPack={() => {
          if (selectedPackId) {
            setPackPreviewId(selectedPackId);
          }
        }}
        onImportDraftChange={setPackImportDraft}
        onImportPack={() => importPackMutation.mutate()}
        onDeletePack={() => {
          if (selectedPackId) {
            deletePackMutation.mutate(selectedPackId);
          }
        }}
        onReadMemory={() => memoryMutation.mutate()}
        onRefreshMemory={() => refreshMemoryMutation.mutate()}
        onPreviewSandboxDiff={() => sandboxDiffMutation.mutate()}
        onCommitMemory={() => commitMutation.mutate()}
        onDiscardMemory={() => discardMutation.mutate()}
        debugPanelData={debugPanelData}
        onToggleDebug={toggleDebug}
      />
      <MobileDebugLauncher debugPanelData={debugPanelData} />
    </div>
  );
}
