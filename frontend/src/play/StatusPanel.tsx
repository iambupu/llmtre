import { HeartPulseIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { ActiveCharacter } from "@/types";
import { textValue } from "@/play/displayTextSupport";
import type { CharacterMetrics, MetricValue } from "@/play/sceneSupport";
import { resolveStatusEffects, resolveStatusSummary } from "@/play/sceneSupport";

/**
 * 功能：渲染会话角色状态、HP/MP、回合数、沙盒模式和后端状态效果。
 * 入参：characterId（string）：展示用角色 ID；activeCharacter（ActiveCharacter | null）：后端角色快照；
 *   metrics（CharacterMetrics）：已解析 HP/MP；sessionTurn（number）：当前会话回合；
 *   sandboxMode（boolean）：是否为 Shadow 状态；hasSession（boolean）：是否已有会话。
 * 出参：JSX.Element。
 * 异常：不抛异常；无会话或字段缺失时统一展示占位态。
 */
export function StatusPanel({
  characterId,
  activeCharacter,
  metrics,
  sessionTurn,
  sandboxMode,
  hasSession,
}: {
  characterId: string;
  activeCharacter: ActiveCharacter | null;
  metrics: CharacterMetrics;
  sessionTurn: number;
  sandboxMode: boolean;
  hasSession: boolean;
}) {
  const name = hasSession
    ? textValue(activeCharacter?.name ?? activeCharacter?.label, "旅行者")
    : "--";
  const shownCharacterId = hasSession ? characterId || "--" : "--";
  const shownTurn = hasSession ? String(sessionTurn) : "--";
  const statusSummary = hasSession ? resolveStatusSummary(activeCharacter) : "--";
  const statusEffects = hasSession ? resolveStatusEffects(activeCharacter) : [];
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HeartPulseIcon data-icon="inline-start" />
          角色状态
        </CardTitle>
        <CardAction>
          <Badge variant={sandboxMode ? "secondary" : "outline"}>{sandboxMode ? "Shadow" : "Active"}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="flex size-14 items-center justify-center rounded-lg border bg-primary/10 font-semibold text-primary">
            {name.slice(0, 2)}
          </div>
          <div>
            <div className="font-medium">{name}</div>
            <div className="text-sm text-muted-foreground">{shownCharacterId}</div>
            <div className="text-xs text-muted-foreground">回合 {shownTurn}</div>
          </div>
        </div>
        <Metric label="HP" value={hasSession ? metrics.hp : null} />
        <Metric label="MP" value={hasSession ? metrics.mp : null} />
        <div className="rounded-lg border bg-muted/30 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">状态摘要</span>
            <Badge variant={statusEffects.length ? "secondary" : "outline"}>
              {statusSummary}
            </Badge>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {hasSession ? (
              statusEffects.length ? (
                statusEffects.map((effect) => (
                  <Badge key={effect.key} variant="outline" title={effect.description}>
                    {effect.label}
                  </Badge>
                ))
              ) : (
                <Badge variant="outline">状态稳定</Badge>
              )
            ) : (
              <Badge variant="outline">--</Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * 功能：渲染单个数值条。
 * 入参：label（string）：指标名；value（MetricValue | null）：当前值与上限，null 表示未知。
 * 出参：JSX.Element。
 * 异常：不抛异常；max 非正或 value 缺失时进度条显示 0。
 */
function Metric({ label, value }: { label: string; value: MetricValue | null }) {
  const percentage =
    value && value.max > 0
      ? Math.max(0, Math.min(100, (value.current / value.max) * 100))
      : 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <strong>{value ? `${value.current} / ${value.max}` : "-- / --"}</strong>
      </div>
      <Progress value={percentage} />
    </div>
  );
}
