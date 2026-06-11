import { Badge } from "@/components/ui/badge";
import { SceneMedia } from "@/components/SceneMedia";
import type { SceneObjectRef, SceneSnapshot } from "@/types";
import { cn } from "@/lib/utils";
import { ActionGroupList } from "@/play/ActionGroupList";
import { groupActionsForDisplay } from "@/play/actionSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import {
  resolveSceneAsset,
  resolveSceneObjectDescription,
  resolveSceneObjectDisplayLabel,
  resolveSceneObjectTypeLabel,
} from "@/play/sceneSupport";

/**
 * 功能：渲染单个场景对象卡片，并把对象标题与动作按钮中的系统 ID 转为玩家可读文本；NPC 使用 1:1 头像图。
 * 入参：item（SceneObjectRef）：当前对象；scene（SceneSnapshot | null）：当前场景快照；
 *   quickActions（string[]）：后端可提交动作；
 *   sceneDisplayResolver（SceneDisplayLabelResolver）：系统 ID 到显示名映射；
 *   isBusy（boolean）：回合请求状态；onSubmit（函数）：提交原始动作值。
 * 出参：JSX.Element，包含对象标题、类型、描述和可点击动作。
 * 异常：不抛异常；缺失显示名时降级为对象标签或“可交互目标”。
 */
export function SceneObjectCard({
  item,
  scene,
  quickActions,
  sceneDisplayResolver,
  isBusy,
  onSubmit,
}: {
  item: SceneObjectRef;
  scene: SceneSnapshot | null;
  quickActions: string[];
  sceneDisplayResolver: SceneDisplayLabelResolver;
  isBusy: boolean;
  onSubmit: (value: string) => Promise<void>;
}) {
  const actionGroups = groupActionsForDisplay(quickActions, null, sceneDisplayResolver);
  const displayLabel = resolveSceneObjectDisplayLabel(item, sceneDisplayResolver);
  const displayDescription = resolveSceneObjectDescription(item, sceneDisplayResolver);
  const objectTypeLabel = resolveSceneObjectTypeLabel(item.object_type);
  const isNpcObject = item.object_type === "npc";
  const assetSource = {
    ...(item.source_ref ?? {}),
    ...item,
  };
  const objectAsset = resolveSceneAsset(
    scene,
    assetSource,
    ["portrait_asset_id", "icon_asset_id", "image_asset_id", "asset_id"],
    ["portrait_asset_url", "icon_asset_url", "image_asset_url", "asset_url"]
  );
  return (
    <div className="rounded-lg border border-primary/20 bg-muted/30 p-3">
      <div className={cn("min-w-0", isNpcObject ? "flex items-start gap-3" : "")}>
        <SceneMedia asset={objectAsset} variant={isNpcObject ? "avatar" : "thumb"} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <strong className="text-sm">{displayLabel}</strong>
            <Badge variant="outline">{objectTypeLabel}</Badge>
          </div>
          <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
            {displayDescription}
          </p>
          {actionGroups.length ? (
            <div className="mt-3">
              <ActionGroupList
                groups={actionGroups}
                isBusy={isBusy}
                compact
                onSubmit={onSubmit}
              />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
