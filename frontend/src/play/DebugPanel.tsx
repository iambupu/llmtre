import {
  ChevronDownIcon,
  ChevronLeftIcon,
  FilterIcon,
  FlaskConicalIcon,
  MenuIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { SessionPayload, TurnResult } from "@/types";
import { DebugPre } from "@/play/DebugPre";
import { DebugStatCard } from "@/play/DebugStatCard";
import {
  formatStageDelta,
  formatTraceDuration,
  resolveDebugErrorCount,
  resolveTraceStages,
} from "@/play/debugSupport";

export type DebugPanelProps = {
  lastRequest: unknown;
  lastSseEvent: unknown;
  logs: unknown[];
  turnData: TurnResult | null;
  sessionData: SessionPayload | null;
  memoryText: string;
  backendPayload: unknown;
  compact?: boolean;
  onCollapse?: () => void;
};

export type DebugPanelData = Omit<DebugPanelProps, "compact" | "onCollapse">;

/**
 * 功能：读取回合的快捷动作布局，供调试状态摘要使用。
 * 入参：turnData（TurnResult | null）：最近回合。
 * 出参：TurnResult["quick_action_layout"] | null。
 * 异常：不抛异常；缺失时返回 null。
 */
function readQuickActionLayout(turnData: TurnResult | null): TurnResult["quick_action_layout"] | null {
  return turnData ? (turnData.quick_action_layout ?? null) : null;
}

/**
 * 功能：读取分支后果数组，供调试状态摘要展示。
 * 入参：turnData（TurnResult | null）：最近回合。
 * 出参：unknown[]，缺失或非数组时返回空数组。
 * 异常：不抛异常。
 */
function readBranchConsequences(turnData: TurnResult | null): unknown[] {
  const consequences = turnData ? turnData.branch_consequences : null;
  return Array.isArray(consequences) ? consequences : [];
}

/**
 * 功能：构造状态页签的结构化调试载荷，避免 DebugPanel 主组件累积可选链复杂度。
 * 入参：turnData（TurnResult | null）：最近回合；activeCharacter（unknown）：当前角色摘要。
 * 出参：Record<string, unknown>，供 DebugPre 展示。
 * 异常：不抛异常；缺失字段按空数组或 null 降级。
 */
function buildStatusDebugValue(
  turnData: TurnResult | null,
  activeCharacter: SessionPayload["active_character"] | TurnResult["active_character"] | null
) {
  const quickActionLayout = readQuickActionLayout(turnData);
  const branchConsequences = readBranchConsequences(turnData);
  return {
    character_state_flags: activeCharacter?.state_flags ?? [],
    character_status_effects: activeCharacter?.status_effects ?? [],
    character_status_context: activeCharacter?.status_context ?? null,
    layout_common_count: quickActionLayout?.common_actions?.length ?? 0,
    layout_object_keys: Object.keys(quickActionLayout?.object_actions ?? {}),
    layout_unmapped_actions: quickActionLayout?.diagnostics?.unmapped_actions ?? [],
    layout_fallback_used: quickActionLayout == null,
    branch_consequences_count: branchConsequences.length,
    branch_consequences: branchConsequences,
  };
}

/**
 * 功能：渲染移动端固定调试入口，桌面端调试面板由 PlayContent 控制。
 * 入参：debugPanelData（DebugPanelData）：调试面板所需状态快照。
 * 出参：JSX.Element。
 * 异常：不抛异常；缺失调试数据时 DebugSheet 内部按空对象展示。
 */
export function MobileDebugLauncher({ debugPanelData }: { debugPanelData: DebugPanelData }) {
  return (
    <div className="fixed top-3 left-3 z-50 xl:hidden">
      <Sheet>
        <Tooltip>
          <TooltipTrigger asChild>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" aria-label="打开调试面板">
                <MenuIcon data-icon="inline-start" />
              </Button>
            </SheetTrigger>
          </TooltipTrigger>
          <TooltipContent>调试面板</TooltipContent>
        </Tooltip>
        <DebugSheet {...debugPanelData} />
      </Sheet>
    </div>
  );
}

/**
 * 功能：渲染与左上调试入口同侧展开的移动端抽屉版调试面板内容。
 * 入参：props（DebugPanelProps）：请求、SSE、Trace、会话与后端原始载荷快照。
 * 出参：JSX.Element。
 * 异常：不抛异常；具体空态和序列化失败由 DebugPanel / DebugPre 降级处理。
 */
export function DebugSheet(props: DebugPanelProps) {
  return (
    <SheetContent side="left" className="w-[92vw] sm:max-w-xl">
      <SheetHeader>
        <SheetTitle>调试面板</SheetTitle>
        <SheetDescription>请求、SSE、Trace 与记忆快照。</SheetDescription>
      </SheetHeader>
      <div className="min-h-0 flex-1 px-4 pb-4">
        <DebugPanel {...props} compact />
      </div>
    </SheetContent>
  );
}

/**
 * 功能：渲染桌面或抽屉内调试面板，展示状态、Trace、日志、记忆和后端原始数据。
 * 入参：DebugPanelProps，compact 控制抽屉样式，onCollapse 控制桌面收起按钮。
 * 出参：JSX.Element。
 * 异常：不抛异常；缺失 trace 时回退到日志列表，JSON 序列化失败交由 stringifyDebug 降级。
 */
export function DebugPanel({
  lastRequest,
  lastSseEvent,
  logs,
  turnData,
  sessionData,
  memoryText,
  backendPayload,
  compact = false,
  onCollapse,
}: DebugPanelProps) {
  const activeCharacter = turnData?.active_character ?? sessionData?.active_character ?? null;
  const traceStages = resolveTraceStages(turnData);
  const traceRows = traceStages.length
    ? traceStages.slice(-10).map((stage, index, rows) => ({
        at: stage.at || "未记录",
        title: `${stage.stage} · ${stage.status}`,
        cost: formatStageDelta(stage.at, rows[index - 1]?.at),
      }))
    : logs.slice(-10).map((entry) => ({
        at: "未记录",
        title: String(entry),
        cost: "未记录",
      }));
  const eventCount = logs.length + Number(Boolean(turnData));
  const errorCount = resolveDebugErrorCount(turnData, lastSseEvent);
  const statusValue = eventCount ? (errorCount ? `失败 ${errorCount}` : "未见错误") : "--";
  const durationValue = formatTraceDuration(traceStages);
  const tokenCount = turnData?.trace
    ? JSON.stringify(turnData.trace).length
    : JSON.stringify({ lastRequest, lastSseEvent }).length;
  return (
    <Card className={cn("h-full overflow-hidden border-primary/20", compact && "border-0 shadow-none ring-0")}>
      <CardHeader className="border-b border-primary/20 bg-card/65">
        <CardTitle className="flex items-center gap-2">
          <FlaskConicalIcon data-icon="inline-start" />
          控制台 / 调试信息
        </CardTitle>
        <CardAction>
          <Button variant="ghost" size="icon" onClick={onCollapse} disabled={!onCollapse} aria-label="收起调试面板">
            <ChevronLeftIcon data-icon="inline-start" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue="trace" className="flex flex-col gap-0">
          <TabsList
            variant="line"
            className="grid h-14 w-full grid-cols-5 rounded-none border-b border-primary/20 bg-card/45 p-0"
          >
            <TabsTrigger className="rounded-none text-base" value="status">状态</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="trace">Trace</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="logs">日志</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="memory">内存</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="backend">后端原始</TabsTrigger>
          </TabsList>
          <TabsContent className="m-0 p-4" value="status">
            <div className="grid grid-cols-2 gap-2">
              <DebugStatCard label="总事件" value={String(eventCount)} />
              <DebugStatCard label="状态" value={statusValue} />
              <DebugStatCard label="总耗时" value={durationValue} />
              <DebugStatCard label="Tokens" value={String(tokenCount)} />
            </div>
            <DebugPre
              value={buildStatusDebugValue(turnData, activeCharacter)}
            />
            <DebugPre value={{ sessionData, turnData }} />
            <DebugPre value={{ stream_done_quick_actions: turnData?.quick_actions ?? [] }} />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="trace">
            <div className="grid grid-cols-4 gap-3">
              <DebugStatCard label="总事件" value={String(eventCount)} />
              <DebugStatCard label="状态" value={statusValue} />
              <DebugStatCard label="总耗时" value={durationValue} />
              <DebugStatCard label="Tokens" value={String(tokenCount)} />
            </div>
            <div className="mt-4 flex gap-2">
              <div className="relative flex-1">
                <SearchIcon data-icon="inline-start" className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground" />
                <Input className="pl-9" placeholder="搜索事件、节点或内容..." />
              </div>
              <Button variant="outline">
                <FilterIcon data-icon="inline-start" />
                筛选
              </Button>
              <Button variant="outline">
                <Trash2Icon data-icon="inline-start" />
                清空
              </Button>
            </div>
            <ScrollArea className="mt-4 h-[560px] rounded-lg border border-primary/20 bg-muted/20">
              <div className="p-3">
                {traceRows.length ? (
                  traceRows.map((row, idx) => (
                    <div key={`${row.at}-${idx}`} className="grid grid-cols-[112px_minmax(0,1fr)]">
                      <div className="relative border-r border-primary/20 py-3 pr-4">
                        <span className="absolute top-5 -right-[5px] size-2 rounded-full bg-primary shadow-[0_0_16px_hsl(var(--primary))]" />
                        <div className="flex flex-col gap-1">
                          <p className="text-sm text-muted-foreground">{row.at}</p>
                          <p className="text-sm text-primary">{row.cost}</p>
                        </div>
                      </div>
                      <div className="border-b border-primary/10 py-3 pl-5">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-base font-semibold">{row.title}</p>
                            <p className="mt-1 text-sm text-muted-foreground">节点执行与数据回流。</p>
                            <p className="mt-2 text-xs text-muted-foreground">通道：stream 分片：{idx + 1}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge variant={row.title.includes("failed") ? "destructive" : "secondary"}>
                              {row.title.includes("failed") ? "失败" : "记录"}
                            </Badge>
                            <ChevronDownIcon data-icon="inline-end" />
                          </div>
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-lg border border-primary/15 bg-background/30 p-4 text-sm text-muted-foreground">
                    暂无 Trace 事件。创建或载入会话并提交一轮后，这里会显示请求与流式事件。
                  </div>
                )}
              </div>
            </ScrollArea>
          </TabsContent>
          <TabsContent className="m-0 p-4" value="logs">
            <DebugPre value={{ lastRequest, lastSseEvent, trace: turnData?.trace }} />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="memory">
            <DebugPre
              value={{
                memoryText,
                memory_summary:
                  turnData?.memory_summary ?? sessionData?.memory_summary,
              }}
            />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="backend">
            <DebugPre value={backendPayload} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
