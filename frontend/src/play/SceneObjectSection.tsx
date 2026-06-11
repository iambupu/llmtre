import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { SceneObjectRef, SceneSnapshot } from "@/types";
import type { SceneQuickActionLayout } from "@/play/actionSupport";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { SceneObjectCard } from "@/play/SceneObjectCard";
import { resolveObjectActionsForDisplay } from "@/play/sceneObjectActions";

/**
 * 功能：按对象类型渲染一个场景交互分区，避免 NPC 与地点在同一视觉列表内混杂。
 * 入参：title（string）：分区标题；emptyText（string）：空态文案；icon（LucideIcon）：标题图标；
 *   items（SceneObjectRef[]）：本分区对象；scene（SceneSnapshot | null）：后端场景快照；
 *   sceneQuickActionLayout（SceneQuickActionLayout）：动作布局；
 *   sceneDisplayResolver（SceneDisplayLabelResolver）：系统 ID 到显示名的前端映射；
 *   isBusy（boolean）：回合请求状态；onSubmit（函数）：快捷动作提交回调。
 * 出参：JSX.Element，包含分区标题、数量、对象卡片或空态。
 * 异常：不抛异常；对象数量为 0 时展示空态，不影响其他分区渲染。
 */
export function SceneObjectSection({
  title,
  emptyText,
  icon: Icon,
  items,
  scene,
  sceneQuickActionLayout,
  sceneDisplayResolver,
  isBusy,
  onSubmit,
}: {
  title: string;
  emptyText: string;
  icon: LucideIcon;
  items: SceneObjectRef[];
  scene: SceneSnapshot | null;
  sceneQuickActionLayout: SceneQuickActionLayout;
  sceneDisplayResolver: SceneDisplayLabelResolver;
  isBusy: boolean;
  onSubmit: (value: string) => Promise<void>;
}) {
  return (
    <section className="min-h-[180px] rounded-lg border border-primary/20 bg-background/35 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Icon data-icon="inline-start" />
          {title}
        </h3>
        <Badge variant="secondary">{items.length}</Badge>
      </div>
      {items.length ? (
        <div className="grid gap-2">
          {items.slice(0, 4).map((item) => (
            <SceneObjectCard
              key={item.object_id}
              item={item}
              scene={scene}
              quickActions={resolveObjectActionsForDisplay(
                item,
                sceneQuickActionLayout,
                scene
              )}
              sceneDisplayResolver={sceneDisplayResolver}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
          ))}
        </div>
      ) : (
        <div className="flex min-h-24 items-center rounded-md border border-dashed border-primary/20 px-3 text-sm text-muted-foreground">
          {emptyText}
        </div>
      )}
    </section>
  );
}
