import { useEffect, useMemo, useRef } from "react";
import {
  ChevronDownIcon,
  EyeOffIcon,
  LoaderCircleIcon,
  MessageSquareTextIcon,
  SendIcon,
  SquareIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { TurnResult } from "@/types";
import { MessageBubble } from "@/play/MessageBubble";
import { nowClock } from "@/play/persistenceSupport";
import type { ChatMessage } from "@/play/persistenceSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { TurnOutcomeSummary } from "@/play/TurnOutcomeSummary";

/**
 * 功能：渲染回合记录与玩家输入区，并复用场景显示名映射修饰聊天快捷动作。
 * 入参：messages（ChatMessage[]）：历史消息；turnData（TurnResult | null）：最近回合；
 *   sessionTurn（number）：会话当前回合数；hasSession（boolean）：是否已加载会话；
 *   streamingText（string）：SSE 流式文本；isBusy（boolean）：请求状态；
 *   userInput（string）：输入框内容；outputMode（"sync" | "stream"）：提交模式；
 *   sceneDisplayResolver（SceneDisplayLabelResolver）：系统 ID 到显示名映射；
 *   onInputChange/onSubmit/onAbort/onModeChange：输入、提交、停止和模式切换回调。
 * 出参：JSX.Element，包含消息列表、快捷动作、输入框和提交控件。
 * 异常：不抛异常；空消息时展示等待态，快捷动作缺失时不渲染按钮组。
 */
export function ChatPanel({
  messages,
  turnData,
  sessionTurn,
  hasSession,
  streamingText,
  isBusy,
  userInput,
  outputMode,
  sceneDisplayResolver,
  onInputChange,
  onSubmit,
  onAbort,
  onModeChange,
}: {
  messages: ChatMessage[];
  turnData: TurnResult | null;
  sessionTurn: number;
  hasSession: boolean;
  streamingText: string;
  isBusy: boolean;
  userInput: string;
  outputMode: "sync" | "stream";
  sceneDisplayResolver: SceneDisplayLabelResolver;
  onInputChange: (value: string) => void;
  onSubmit: (value: string) => Promise<void>;
  onAbort: () => void;
  onModeChange: (value: "sync" | "stream") => void;
}) {
  const hasMessages = messages.length > 0 || streamingText.length > 0;
  const modeLabel = outputMode === "stream" ? "流式" : "普通";
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const actionMessageIndex = useMemo(
    () =>
      [...messages]
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => item.role === "gm" && (item.quickActions?.length ?? 0) > 0)
        .map((item) => item.index)
        .pop(),
    [messages]
  );
  const latestQuickActions = useMemo(
    () =>
      actionMessageIndex !== undefined ? messages[actionMessageIndex]?.quickActions ?? [] : [],
    [actionMessageIndex, messages]
  );
  const latestHistoryText = useMemo(() => {
    const latestGm = [...messages].reverse().find((item) => item.role === "gm");
    return latestGm?.text ?? "";
  }, [messages]);
  useEffect(() => {
    // Radix ScrollArea 的真实滚动节点是 viewport；bottomRef 只是消息末尾锚点。
    const viewport = bottomRef.current?.closest<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    );
    if (!viewport) {
      return;
    }
    // 滚动边界：聊天记录排到窄屏底部后，只允许消息框内部跟随最新消息，
    // 不能用 scrollIntoView 触发整页跳到回合记录。
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
  }, [messages, streamingText, latestQuickActions, isBusy]);
  return (
    <Card className="border-primary/20">
      <CardHeader className="border-b border-primary/20">
        <CardTitle className="flex items-center gap-2">
          <MessageSquareTextIcon data-icon="inline-start" />
          回合记录
        </CardTitle>
        <CardDescription>输入行动或点击建议行动推进回合</CardDescription>
        <CardAction>
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{modeLabel}</Badge>
            <Button variant="ghost" size="sm" disabled={!hasMessages}>
              <EyeOffIcon data-icon="inline-start" />
              清空记录
            </Button>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pt-4">
        <TurnOutcomeSummary
          turnData={turnData}
          sessionTurn={sessionTurn}
          hasSession={hasSession}
          latestHistoryText={latestHistoryText}
          isBusy={isBusy}
          streamingText={streamingText}
          sceneDisplayResolver={sceneDisplayResolver}
        />
        <ScrollArea className="h-[360px] rounded-lg border border-primary/20 bg-muted/20">
          <div className="flex flex-col gap-3 p-3">
            {messages.map((message, idx) => (
              <MessageBubble
                key={`${message.role}-${idx}`}
                message={message}
                quickActions={idx === actionMessageIndex ? message.quickActions ?? [] : []}
                quickActionsDisabled={isBusy}
                sceneDisplayResolver={sceneDisplayResolver}
                onQuickAction={onSubmit}
              />
            ))}
            {isBusy && streamingText ? (
              <MessageBubble
                message={{ role: "gm", text: streamingText, at: nowClock() }}
                sceneDisplayResolver={sceneDisplayResolver}
                streaming
              />
            ) : null}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
        <Separator />
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <Textarea
            value={userInput}
            onChange={(e) => onInputChange(e.target.value)}
            placeholder="输入命令或对话，例如：观察周围"
            disabled={isBusy}
            className="h-28 resize-none border-primary/20 bg-background/40"
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) {
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void onSubmit(userInput);
              }
            }}
          />
          <div className="flex flex-col gap-2 lg:min-w-[220px]">
            <ToggleGroup
              type="single"
              value={outputMode}
              onValueChange={(value) => value && onModeChange(value as "sync" | "stream")}
              variant="outline"
            >
              <ToggleGroupItem value="stream">流式</ToggleGroupItem>
              <ToggleGroupItem value="sync">普通</ToggleGroupItem>
            </ToggleGroup>
            <p className="text-xs text-muted-foreground">
              {outputMode === "stream"
                ? "流式：逐段返回叙事，首字更快。"
                : "普通：一次性返回完整结果。"}
            </p>
            <div className="grid grid-cols-[1fr_auto] gap-2">
              <Button disabled={isBusy || !userInput.trim()} onClick={() => void onSubmit(userInput)}>
                {isBusy ? (
                  <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
                ) : (
                  <SendIcon data-icon="inline-start" />
                )}
                {isBusy ? "处理中..." : "发送"}
              </Button>
              <Button variant="outline" size="icon" disabled={isBusy || !userInput.trim()}>
                <ChevronDownIcon data-icon="inline-start" />
              </Button>
            </div>
            <Button variant="outline" disabled={!isBusy} onClick={onAbort}>
              <SquareIcon data-icon="inline-start" />
              停止
            </Button>
            <p className="text-xs text-primary/90">
              {isBusy
                ? outputMode === "stream"
                  ? "正在流式生成，请稍候或点击停止。"
                  : "正在请求完整回合结果，请稍候。"
                : "已就绪，输入行动后点击发送。"}
            </p>
            <p className="text-xs text-muted-foreground">Enter 发送，Shift + Enter 换行</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
