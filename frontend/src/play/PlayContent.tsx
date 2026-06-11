import { ChevronLeftIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  ActiveCharacter,
  SceneSnapshot,
  StoryPackDetailPayload,
  StoryPackSummary,
  TurnResult,
} from "@/types";
import { ChatPanel } from "@/play/ChatPanel";
import { DebugPanel } from "@/play/DebugPanel";
import type { DebugPanelData } from "@/play/DebugPanel";
import { InventoryPanel } from "@/play/InventoryPanel";
import { MemoryPanel } from "@/play/MemoryPanel";
import { PackReleasePanel } from "@/play/PackReleasePanel";
import { QuestPanel } from "@/play/QuestPanel";
import { ScenePanel } from "@/play/ScenePanel";
import { StatusPanel } from "@/play/StatusPanel";
import type { SceneQuickActionLayout } from "@/play/actionSupport";
import type { ChatMessage } from "@/play/persistenceSupport";
import type { CharacterMetrics, SceneDisplayLabelResolver } from "@/play/sceneSupport";

type PlayContentProps = {
  debugVisible: boolean;
  scene: SceneSnapshot | null;
  turnData: TurnResult | null;
  isBusy: boolean;
  playerGoal: string;
  sceneQuickActionLayout: SceneQuickActionLayout;
  sceneDisplayResolver: SceneDisplayLabelResolver;
  submitTurn: (value: string) => Promise<void>;
  messages: ChatMessage[];
  streamingText: string;
  userInput: string;
  outputMode: "sync" | "stream";
  onInputChange: (value: string) => void;
  onModeChange: (value: "sync" | "stream") => void;
  onAbort: () => void;
  sessionCharacterId: string;
  characterId: string;
  activeCharacter: ActiveCharacter | null;
  metrics: CharacterMetrics;
  sessionTurn: number;
  sandboxMode: boolean;
  hasSession: boolean;
  inventory: unknown[];
  quests: Record<string, unknown>[];
  storyPacks: StoryPackSummary[];
  storyPackDiagnostics: Record<string, string[]>;
  selectedPack: StoryPackSummary | null;
  packPreview: StoryPackDetailPayload | null;
  packImportDraft: string;
  sessionId: string;
  displayedMemory: string;
  packPanelDisabled: boolean;
  storyPacksLoading: boolean;
  packPreviewLoading: boolean;
  packImporting: boolean;
  packDeleting: boolean;
  onPreviewPack: () => void;
  onImportDraftChange: (value: string) => void;
  onImportPack: () => void;
  onDeletePack: () => void;
  onReadMemory: () => void;
  onRefreshMemory: () => void;
  onCommitMemory: () => void;
  onDiscardMemory: () => void;
  debugPanelData: DebugPanelData;
  onToggleDebug: () => void;
};

/**
 * 功能：渲染游玩页主体三栏布局，承载场景、聊天、状态侧栏和桌面调试面板；
 *   窄布局下将回合记录排在状态/背包/任务之后，避免玩家信息面板被聊天流挤到页面底部。
 * 入参：PlayContentProps，包含 App 已解析好的展示数据和事件回调。
 * 出参：JSX.Element。
 * 异常：不抛异常；空会话、空场景和空调试数据由各子面板自行降级展示。
 */
export function PlayContent({
  debugVisible,
  scene,
  turnData,
  isBusy,
  playerGoal,
  sceneQuickActionLayout,
  sceneDisplayResolver,
  submitTurn,
  messages,
  streamingText,
  userInput,
  outputMode,
  onInputChange,
  onModeChange,
  onAbort,
  sessionCharacterId,
  characterId,
  activeCharacter,
  metrics,
  sessionTurn,
  sandboxMode,
  hasSession,
  inventory,
  quests,
  storyPacks,
  storyPackDiagnostics,
  selectedPack,
  packPreview,
  packImportDraft,
  sessionId,
  displayedMemory,
  packPanelDisabled,
  storyPacksLoading,
  packPreviewLoading,
  packImporting,
  packDeleting,
  onPreviewPack,
  onImportDraftChange,
  onImportPack,
  onDeletePack,
  onReadMemory,
  onRefreshMemory,
  onCommitMemory,
  onDiscardMemory,
  debugPanelData,
  onToggleDebug,
}: PlayContentProps) {
  return (
    <main
      className={cn(
        "mx-auto grid max-w-[1760px] gap-4 px-4 py-4",
        debugVisible
          ? "xl:grid-cols-[minmax(0,1.35fr)_320px_460px]"
          : "xl:grid-cols-[minmax(0,1.62fr)_320px_48px]"
      )}
    >
      {/* 响应式排序：窄屏时让 section 不生成额外布局盒，场景和回合记录可独立参与主网格排序。 */}
      <section className="contents xl:flex xl:min-w-0 xl:flex-col xl:gap-4">
        <div className="order-1 min-w-0 xl:order-none">
          <ScenePanel
            scene={scene}
            turnData={turnData}
            isBusy={isBusy}
            sessionTurn={sessionTurn}
            hasSession={hasSession}
            playerGoal={playerGoal}
            sceneQuickActionLayout={sceneQuickActionLayout}
            sceneDisplayResolver={sceneDisplayResolver}
            onSubmit={submitTurn}
          />
        </div>
        {/* 窄屏阅读顺序：回合记录放到状态/任务/记忆之后；桌面端恢复为左侧主栏第二块。 */}
        <div className="order-3 min-w-0 xl:order-none">
          <ChatPanel
            messages={messages}
            turnData={turnData}
            sessionTurn={sessionTurn}
            hasSession={hasSession}
            streamingText={streamingText}
            isBusy={isBusy}
            userInput={userInput}
            outputMode={outputMode}
            sceneDisplayResolver={sceneDisplayResolver}
            onInputChange={onInputChange}
            onSubmit={submitTurn}
            onAbort={onAbort}
            onModeChange={onModeChange}
          />
        </div>
      </section>

      {/* 侧栏是玩家续玩判断的核心信息，窄屏下必须先于聊天历史展示。 */}
      <aside className="order-2 flex min-w-0 flex-col gap-4 xl:order-none">
        <StatusPanel
          characterId={sessionCharacterId || characterId}
          activeCharacter={activeCharacter}
          metrics={metrics}
          sessionTurn={sessionTurn}
          sandboxMode={sandboxMode}
          hasSession={hasSession}
        />
        <InventoryPanel inventory={inventory} />
        <QuestPanel quests={quests} />
        <PackReleasePanel
          storyPacks={storyPacks}
          diagnostics={storyPackDiagnostics}
          selectedPack={selectedPack}
          preview={packPreview}
          importDraft={packImportDraft}
          disabled={packPanelDisabled}
          isLoading={storyPacksLoading}
          isPreviewLoading={packPreviewLoading}
          isImporting={packImporting}
          isDeleting={packDeleting}
          onPreview={onPreviewPack}
          onImportDraftChange={onImportDraftChange}
          onImport={onImportPack}
          onDelete={onDeletePack}
        />
        <MemoryPanel
          memoryText={displayedMemory}
          sceneDisplayResolver={sceneDisplayResolver}
          disabled={!sessionId}
          onRead={onReadMemory}
          onRefresh={onRefreshMemory}
          onCommit={onCommitMemory}
          onDiscard={onDiscardMemory}
        />
      </aside>

      {debugVisible ? (
        <aside className="hidden min-w-0 xl:block">
          <DebugPanel {...debugPanelData} onCollapse={onToggleDebug} />
        </aside>
      ) : (
        <aside className="hidden items-center justify-start xl:flex">
          <Button variant="ghost" size="icon" onClick={onToggleDebug}>
            <ChevronLeftIcon data-icon="inline-start" />
          </Button>
        </aside>
      )}
    </main>
  );
}
