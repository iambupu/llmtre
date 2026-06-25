import { ScrollTextIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { textValue } from "@/play/displayTextSupport";
import {
  resolveQuestStageDescription,
  resolveQuestStageTitle,
} from "@/play/questPanelSupport";

/**
 * 功能：渲染当前场景活跃任务列表。
 * 入参：quests（Record<string, unknown>[]）：后端场景快照中的任务对象列表。
 * 出参：JSX.Element。
 * 异常：不抛异常；缺少标题、阶段或状态时使用任务 ID、状态或占位文案降级。
 */
export function QuestPanel({ quests }: { quests: Record<string, unknown>[] }) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScrollTextIcon data-icon="inline-start" />
          当前任务
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {quests.length ? (
          quests.slice(0, 4).map((quest, idx) => {
            const progressLabel = textValue(quest.progress_label, "");
            const stageDescription = resolveQuestStageDescription(quest);

            return (
              <div key={idx} className="rounded-lg border bg-muted/30 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <strong className="text-sm">
                    {textValue(quest.name ?? quest.title ?? quest.quest_id, "未命名任务")}
                  </strong>
                  <Badge variant="outline">
                    {textValue(quest.status_label ?? quest.status, "未知")}
                  </Badge>
                  {progressLabel ? <Badge variant="secondary">{progressLabel}</Badge> : null}
                </div>
                <p className="mt-2 text-sm font-medium">{resolveQuestStageTitle(quest)}</p>
                {stageDescription ? (
                  <p className="mt-1 text-sm text-muted-foreground">{stageDescription}</p>
                ) : null}
              </div>
            );
          })
        ) : (
          <p className="text-sm text-muted-foreground">暂无活跃任务</p>
        )}
      </CardContent>
    </Card>
  );
}
