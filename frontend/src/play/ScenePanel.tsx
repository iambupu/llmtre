import {
  CompassIcon,
  MapIcon,
  ScrollTextIcon,
  SparklesIcon,
  UserRoundIcon,
  WandSparklesIcon,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SceneMedia } from "@/components/SceneMedia";
import type { SceneSnapshot, TurnResult } from "@/types";
import { ActionGroupList } from "@/play/ActionGroupList";
import { SceneObjectSection } from "@/play/SceneObjectSection";
import type { SceneQuickActionLayout } from "@/play/actionSupport";
import { groupActionsForDisplay } from "@/play/actionSupport";
import { textValue } from "@/play/displayTextSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import {
  resolveSceneAsset,
  resolveSceneDisplayLabelFromRecord,
  resolveSceneStatus,
} from "@/play/sceneSupport";

/**
 * 功能：渲染当前场景总览，并将 NPC 与地点对象拆分为视觉独立的交互区域。
 * 入参：scene（SceneSnapshot | null）：后端场景快照；turnData（TurnResult | null）：最近回合；
 *   isBusy（boolean）：回合请求状态；sessionTurn（number）：当前会话回合数；
 *   hasSession（boolean）：是否已创建或加载会话；playerGoal（string）：玩家可读目标；
 *   sceneQuickActionLayout（SceneQuickActionLayout）：场景动作布局；
 *   sceneDisplayResolver（SceneDisplayLabelResolver）：系统 ID 到显示名的前端映射；
 *   onSubmit（函数）：提交快捷动作或玩家输入。
 * 出参：JSX.Element，包含位置、出口、公共快捷操作、NPC 区、地点区和状态提示。
 * 异常：不抛异常；缺失场景字段时以空列表和占位文案降级展示。
 */
export function ScenePanel({
  scene,
  turnData,
  isBusy,
  sessionTurn,
  hasSession,
  playerGoal,
  sceneQuickActionLayout,
  sceneDisplayResolver,
  onSubmit,
}: {
  scene: SceneSnapshot | null;
  turnData: TurnResult | null;
  isBusy: boolean;
  sessionTurn: number;
  hasSession: boolean;
  playerGoal: string;
  sceneQuickActionLayout: SceneQuickActionLayout;
  sceneDisplayResolver: SceneDisplayLabelResolver;
  onSubmit: (value: string) => Promise<void>;
}) {
  const location = scene?.current_location ?? {};
  const sceneObjects = scene?.scene_objects ?? [];
  const interactionObjects = sceneObjects.filter((item) => item.object_type === "interaction");
  const npcObjects = sceneObjects.filter((item) => item.object_type === "npc");
  const placeObjects = sceneObjects.filter((item) =>
    ["location", "exit", "system"].includes(item.object_type)
  );
  const exits = scene?.exits ?? [];
  const visibleTargets = (scene?.visible_npcs ?? []).map((item) =>
    resolveSceneDisplayLabelFromRecord(
      item,
      ["name", "label", "entity_id", "id"],
      sceneDisplayResolver,
      "目标"
    )
  );
  const visibleItems = (scene?.visible_items ?? []).map((item) =>
    resolveSceneDisplayLabelFromRecord(
      item,
      ["name", "label", "item_id", "id"],
      sceneDisplayResolver,
      "物品"
    )
  );
  const title = resolveSceneDisplayLabelFromRecord(
    location,
    ["name", "label", "location_id", "id"],
    sceneDisplayResolver,
    "未进入场景"
  );
  const description = textValue(
    location.description ?? scene?.ui_hints?.description,
    "创建或加载会话后，当前位置、出口和可见对象会显示在这里。"
  );
  const locationAsset = resolveSceneAsset(
    scene,
    location,
    ["background_asset_id", "image_asset_id", "asset_id"],
    ["background_asset_url", "image_asset_url", "asset_url"]
  );
  const statusText = resolveSceneStatus(turnData, scene);
  const progressBadgeText = isBusy
    ? "等待流式"
    : sessionTurn > 0
      ? `回合 ${sessionTurn}`
      : hasSession
        ? "会话已载入"
        : "等待会话";
  const commonActionGroups = groupActionsForDisplay(
    sceneQuickActionLayout.commonActions,
    scene,
    sceneDisplayResolver
  );
  return (
    <Card className="scene-stage scene-fade-in border-primary/20 overflow-hidden">
      <CardHeader className="border-b border-primary/20">
        <CardTitle className="flex items-center gap-2 text-3xl md:text-5xl">
          <CompassIcon data-icon="inline-start" />
          {title}
        </CardTitle>
        <CardDescription className="text-base">{description}</CardDescription>
        <CardAction>
          <Badge variant="secondary" className="bg-primary/15 text-primary">
            {progressBadgeText}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4 pt-4 lg:grid-cols-[1fr_280px]">
        <div className="flex flex-col gap-4">
          <SceneMedia asset={locationAsset} variant="hero" />
          <div className="rounded-lg border border-primary/25 bg-background/45 p-3">
            <div className="flex items-center gap-2 text-sm text-primary">
              <ScrollTextIcon data-icon="inline-start" />
              当前目标
            </div>
            <p className="mt-2 text-base font-medium leading-6">{playerGoal}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {exits.length ? (
              exits.slice(0, 6).map((exit, idx) => (
                <Badge key={`${textValue(exit.id, "exit")}-${idx}`} variant="outline">
                  <MapIcon data-icon="inline-start" />
                  {resolveSceneDisplayLabelFromRecord(
                    exit,
                    ["name", "label", "to_location_id", "id"],
                    sceneDisplayResolver,
                    "出口"
                  )}
                </Badge>
              ))
            ) : (
              <Badge variant="outline">暂无出口信息</Badge>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">
              目标：{visibleTargets.length ? visibleTargets.join("、") : "无可见目标"}
            </Badge>
            <Badge variant="outline">
              可互动物品：{visibleItems.length ? visibleItems.join("、") : "无可见物品"}
            </Badge>
          </div>
          {commonActionGroups.length ? (
            <ActionGroupList
              title="可选行动"
              groups={commonActionGroups}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
          ) : null}
          <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
            <SceneObjectSection
              title="互动"
              emptyText="当前没有关键互动"
              icon={WandSparklesIcon}
              items={interactionObjects}
              scene={scene}
              sceneQuickActionLayout={sceneQuickActionLayout}
              sceneDisplayResolver={sceneDisplayResolver}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
            <SceneObjectSection
              title="NPC"
              emptyText="当前没有可见 NPC"
              icon={UserRoundIcon}
              items={npcObjects}
              scene={scene}
              sceneQuickActionLayout={sceneQuickActionLayout}
              sceneDisplayResolver={sceneDisplayResolver}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
            <SceneObjectSection
              title="地点"
              emptyText="当前没有地点对象"
              icon={MapIcon}
              items={placeObjects}
              scene={scene}
              sceneQuickActionLayout={sceneQuickActionLayout}
              sceneDisplayResolver={sceneDisplayResolver}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
          </div>
        </div>
        <Alert>
          <SparklesIcon data-icon="inline-start" />
          <AlertTitle>当前状态</AlertTitle>
          <AlertDescription>{statusText}</AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  );
}
