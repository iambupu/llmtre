import {
  ArchiveIcon,
  BugIcon,
  ChevronDownIcon,
  ClockIcon,
  Link2Icon,
  LoaderCircleIcon,
  PackageIcon,
  PlusCircleIcon,
  RotateCcwIcon,
  SparklesIcon,
  Trash2Icon,
  UploadIcon,
  UserRoundIcon,
} from "lucide-react";
import type { SessionSummary, StoryPackSummary } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * 功能：生成会话选择菜单的主标题，优先展示当前场景，避免玩家只能看到裸 ID。
 * 入参：session（SessionSummary）：后端或本地缓存的会话摘要。
 * 出参：string，可直接渲染的标题。
 * 异常：不抛异常；缺少场景标题时按场景 ID 或“未记录场景”降级。
 */
function formatSessionTitle(session: SessionSummary): string {
  return session.current_scene_title || session.current_scene_id || "未记录场景";
}

/**
 * 功能：生成会话选择菜单的剧本包描述。
 * 入参：session（SessionSummary）：会话摘要。
 * 出参：string，优先为剧本标题，其次 pack_id，最后为自定义/未知剧本。
 * 异常：不抛异常；字段缺失时返回稳定兜底文案。
 */
function formatSessionPackLabel(session: SessionSummary): string {
  return session.pack_title || session.pack_id || "自定义或未知剧本";
}

/**
 * 功能：把 ISO 时间压缩成会话菜单里的最近活动时间。
 * 入参：value（string | undefined）：后端返回的 ISO 时间。
 * 出参：string，浏览器本地化后的日期时间或“未记录时间”。
 * 异常：不抛异常；非法时间字符串按未记录降级。
 */
function formatSessionTime(value: string | undefined): string {
  if (!value) {
    return "未记录时间";
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return "未记录时间";
  }
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type PlayHeaderProps = {
  characterId: string;
  sessionId: string;
  sessionChoices: SessionSummary[];
  selectedPackId: string;
  storyPacks: StoryPackSummary[];
  background: string;
  isBusy: boolean;
  storyPacksLoading: boolean;
  sessionsLoading: boolean;
  createPending: boolean;
  sessionDeletePending: boolean;
  canCreateSession: boolean;
  debugVisible: boolean;
  logsCount: number;
  onCharacterIdChange: (value: string) => void;
  onSessionIdChange: (value: string) => void;
  onSelectedPackIdChange: (value: string) => void;
  onBackgroundChange: (value: string) => void;
  onCreateSession: () => void;
  onLoadSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onResetSession: () => void;
  onToggleDebug: () => void;
};

/**
 * 功能：渲染游玩页顶部会话、角色、剧本包和调试入口控制栏。
 * 入参：PlayHeaderProps，包含当前会话状态、加载状态和按钮回调。
 * 出参：JSX.Element。
 * 异常：不抛异常；空列表或空背景按禁用态展示。
 */
export function PlayHeader({
  characterId,
  sessionId,
  sessionChoices,
  selectedPackId,
  storyPacks,
  background,
  isBusy,
  storyPacksLoading,
  sessionsLoading,
  createPending,
  sessionDeletePending,
  canCreateSession,
  debugVisible,
  logsCount,
  onCharacterIdChange,
  onSessionIdChange,
  onSelectedPackIdChange,
  onBackgroundChange,
  onCreateSession,
  onLoadSession,
  onSelectSession,
  onDeleteSession,
  onResetSession,
  onToggleDebug,
}: PlayHeaderProps) {
  const hasSessionMenu = sessionsLoading || sessionChoices.length > 0;
  return (
    <header className="border-b border-primary/20 bg-background/90 backdrop-blur xl:sticky xl:top-0 xl:z-40">
      <div className="mx-auto flex max-w-[1760px] flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center">
        <div className="flex items-center gap-3">
          <div className="tre-logo flex size-14 items-center justify-center rounded-lg border border-primary/40 bg-primary/10">
            <SparklesIcon data-icon="inline-start" />
          </div>
          <div>
            <h1 className="text-3xl font-semibold text-primary">LLM TRE</h1>
            <p className="text-sm text-muted-foreground">
              A2 · 剧本驱动文字 TRPG · 玩家模式
            </p>
          </div>
        </div>
        <div className="grid min-w-0 flex-1 gap-2 md:grid-cols-3 2xl:grid-cols-[minmax(170px,1fr)_minmax(220px,1.2fr)_minmax(220px,1.2fr)_auto_auto_auto_auto_auto]">
          <div className="relative">
            <UserRoundIcon
              data-icon="inline-start"
              className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground"
            />
            <Input
              className="pl-9"
              value={characterId}
              onChange={(event) => onCharacterIdChange(event.target.value)}
            />
          </div>
          <div className="relative z-30">
            <Link2Icon
              data-icon="inline-start"
              className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground"
            />
            <Input
              aria-label="输入或粘贴会话 ID"
              className={`pl-9 ${hasSessionMenu ? "pr-28" : ""}`}
              value={sessionId}
              onChange={(event) => onSessionIdChange(event.target.value)}
              list="llmtre-recent-sessions"
              placeholder="选择或输入会话 ID"
            />
            {hasSessionMenu && (
              <details className="group absolute top-1 right-1">
                <summary
                  aria-label="选择已保存会话"
                  aria-disabled={isBusy}
                  className="flex h-6 cursor-pointer list-none items-center gap-1 rounded border border-primary/20 bg-primary/10 px-2 text-xs text-primary shadow-sm transition hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring aria-disabled:pointer-events-none aria-disabled:opacity-50 [&::-webkit-details-marker]:hidden"
                  onClick={(event) => {
                    if (isBusy) {
                      event.preventDefault();
                    }
                  }}
                >
                  {sessionsLoading ? "同步中" : "已保存"}
                  <ChevronDownIcon
                    data-icon="inline-end"
                    className="size-3 transition-transform group-open:rotate-180"
                  />
                </summary>
                <div className="absolute right-0 top-8 z-50 w-96 max-w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-lg">
                  {sessionChoices.length === 0 ? (
                    <div className="px-3 py-3 text-xs text-muted-foreground">
                      正在读取已保存会话…
                    </div>
                  ) : null}
                  {sessionChoices.map((choice) => (
                    <div
                      key={choice.session_id}
                      className={`grid grid-cols-[minmax(0,1fr)_2rem] items-stretch gap-1 rounded transition ${
                        choice.session_id === sessionId ? "bg-primary/10 text-primary" : ""
                      }`}
                    >
                      <button
                        type="button"
                        className="min-w-0 rounded px-2 py-2 text-left transition hover:bg-primary/10"
                        onClick={(event) => {
                          onSelectSession(choice.session_id);
                          event.currentTarget.closest("details")?.removeAttribute("open");
                        }}
                      >
                        <span className="flex min-w-0 items-center justify-between gap-2">
                          <span className="min-w-0 truncate text-sm font-medium">
                            {formatSessionTitle(choice)}
                          </span>
                          <Badge variant="outline" className="shrink-0">
                            第 {choice.current_session_turn_id} 回合
                          </Badge>
                        </span>
                        <span className="mt-1 flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                          <PackageIcon data-icon="inline-start" className="size-3" />
                          <span className="truncate">{formatSessionPackLabel(choice)}</span>
                          <ClockIcon data-icon="inline-start" className="ml-1 size-3" />
                          <span className="shrink-0">
                            {formatSessionTime(choice.last_active_at)}
                          </span>
                        </span>
                        <span className="mt-1 block truncate font-mono text-[11px] text-muted-foreground">
                          {choice.session_id}
                        </span>
                      </button>
                      <button
                        type="button"
                        aria-label={`删除会话 ${choice.session_id}`}
                        title="删除会话"
                        className="my-1 flex items-center justify-center rounded text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive disabled:pointer-events-none disabled:opacity-40"
                        disabled={isBusy || sessionDeletePending}
                        onClick={(event) => {
                          event.stopPropagation();
                          // 删除会移除后端持久化数据，因此必须让玩家在菜单内明确确认。
                          if (
                            window.confirm(
                              `删除会话 ${formatSessionTitle(choice)}？该会话的回合、记忆和保存进度都会被移除。`
                            )
                          ) {
                            onDeleteSession(choice.session_id);
                          }
                        }}
                      >
                        <Trash2Icon className="size-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {sessionChoices.length > 0 && (
              <datalist id="llmtre-recent-sessions">
                {sessionChoices.map((choice) => (
                  <option key={choice.session_id} value={choice.session_id} />
                ))}
              </datalist>
            )}
          </div>
          <div className="relative">
            <PackageIcon
              data-icon="inline-start"
              className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground"
            />
            <select
              className="h-10 w-full rounded-md border border-input bg-background px-9 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              value={selectedPackId}
              onChange={(event) => onSelectedPackIdChange(event.target.value)}
              disabled={isBusy || storyPacksLoading}
            >
              <option value="">自定义背景生成</option>
              {storyPacks.map((pack: StoryPackSummary) => (
                <option key={pack.pack_id} value={pack.pack_id}>
                  {pack.title} · {pack.version}
                </option>
              ))}
            </select>
          </div>
          {!selectedPackId && (
            <div className="relative col-span-full">
              <Textarea
                className="min-h-[60px] max-h-[120px]"
                value={background}
                onChange={(event) => onBackgroundChange(event.target.value.slice(0, 2000))}
                placeholder="描述你想要的游戏世界…（最多 2000 字符）"
                disabled={isBusy || createPending}
              />
              <span
                className={`absolute bottom-1 right-2 text-xs ${
                  background.length > 1900 ? "text-destructive" : "text-muted-foreground"
                }`}
              >
                {background.length}/2000
              </span>
            </div>
          )}
          <Button onClick={onCreateSession} disabled={isBusy || createPending || !canCreateSession}>
            {createPending ? (
              <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
            ) : (
              <PlusCircleIcon data-icon="inline-start" />
            )}
            新会话
          </Button>
          <Button variant="secondary" disabled={!sessionId || isBusy}>
            <UploadIcon data-icon="inline-start" />
            导出会话
          </Button>
          <Button variant="outline" onClick={onLoadSession} disabled={!sessionId || isBusy}>
            <ArchiveIcon data-icon="inline-start" />
            加载 / 保存
          </Button>
          <Button variant="outline" onClick={onResetSession} disabled={!sessionId || isBusy}>
            <RotateCcwIcon data-icon="inline-start" />
            重置
          </Button>
          <Button variant={debugVisible ? "secondary" : "outline"} onClick={onToggleDebug}>
            <BugIcon data-icon="inline-start" />
            控制台 / 调试
            <Badge variant="outline" className="ml-1">
              {Math.min(logsCount, 9)}
            </Badge>
            <ChevronDownIcon data-icon="inline-end" />
          </Button>
        </div>
      </div>
    </header>
  );
}
