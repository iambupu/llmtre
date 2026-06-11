import { LoaderCircleIcon, SparklesIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { TurnResult } from "@/types";
import { resolveTurnFacts } from "@/play/actionSupport";
import { textValue } from "@/play/displayTextSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { rewriteActionLabelForDisplay } from "@/play/sceneSupport";

/**
 * 功能：展示最近回合的玩家可读结果摘要，突出完成状态、后端事实和下一步建议。
 * 入参：turnData（TurnResult | null）：最近回合结果；sessionTurn（number）：当前会话回合数；
 *   hasSession（boolean）：是否已加载会话；latestHistoryText（string）：历史恢复后的最新 GM 文本；
 *   isBusy（boolean）：是否正在提交；streamingText（string）：SSE 已收到的叙事片段；
 *   sceneDisplayResolver（SceneDisplayLabelResolver）：系统 ID 到显示名映射。
 * 出参：JSX.Element。
 * 异常：不抛异常；没有回合时展示等待提示。
 */
export function TurnOutcomeSummary({
  turnData,
  sessionTurn,
  hasSession,
  latestHistoryText,
  isBusy,
  streamingText,
  sceneDisplayResolver,
}: {
  turnData: TurnResult | null;
  sessionTurn: number;
  hasSession: boolean;
  latestHistoryText: string;
  isBusy: boolean;
  streamingText: string;
  sceneDisplayResolver: SceneDisplayLabelResolver;
}) {
  const facts = resolveTurnFacts(turnData);
  const nextStep = rewriteActionLabelForDisplay(
    textValue(turnData?.suggested_next_step, ""),
    sceneDisplayResolver
  );
  const responseText = rewriteActionLabelForDisplay(
    turnData?.final_response ||
      (sessionTurn > 0 && latestHistoryText
        ? latestHistoryText
        : "选择一个行动，或用自然语言描述你想做什么。"),
    sceneDisplayResolver
  );
  const streamingDisplayText = rewriteActionLabelForDisplay(
    streamingText || "系统正在解析行动、结算状态并生成叙事反馈。",
    sceneDisplayResolver
  );
  const summaryTitle = isBusy
    ? "行动处理中"
    : turnData
      ? `最近结果 · 回合 ${turnData.session_turn_id}`
      : sessionTurn > 0
        ? `最近记录 · 回合 ${sessionTurn}`
        : "等待行动";
  const summaryBadge = isBusy
    ? "生成中"
    : turnData?.failure_reason
      ? "需要修正"
      : turnData
        ? "已完成"
        : hasSession
          ? "已载入"
          : "就绪";
  return (
    <div className="rounded-lg border border-primary/20 bg-background/45 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-primary">
          {isBusy ? (
            <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
          ) : (
            <SparklesIcon data-icon="inline-start" />
          )}
          {summaryTitle}
        </div>
        <Badge variant={turnData?.failure_reason ? "destructive" : "secondary"}>
          {summaryBadge}
        </Badge>
      </div>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        {isBusy ? streamingDisplayText : responseText}
      </p>
      {facts.length || nextStep ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {facts.map((fact) => (
            <Badge key={fact} variant="outline">
              {fact}
            </Badge>
          ))}
          {nextStep ? (
            <Badge variant="secondary">
              下一步：{nextStep}
            </Badge>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
