import { PackageIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { textValue } from "@/play/displayTextSupport";
import { formatSystemIdentifierForDisplay } from "@/play/sceneDisplaySupport";

/**
 * 功能：解析背包物品名称，避免直接显示剧情物品内部 ID。
 * 入参：item（unknown）：后端 inventory 或 inventory_items 条目。
 * 出参：string，优先使用 name/label/item_id 并转成玩家可读文本。
 * 异常：不抛异常；缺失字段时返回“物品”。
 */
export function resolveInventoryItemName(item: unknown): string {
  if (typeof item === "object" && item) {
    const source = item as Record<string, unknown>;
    return formatSystemIdentifierForDisplay(
      textValue(source.name ?? source.label ?? source.item_id, "物品")
    );
  }
  return formatSystemIdentifierForDisplay(textValue(item, "物品"));
}

/**
 * 功能：解析背包物品描述，缺少描述时给出玩家可读兜底。
 * 入参：item（unknown）：后端 inventory 或 inventory_items 条目。
 * 出参：string，适合背包卡片展示的说明。
 * 异常：不抛异常；未知物品按“已取得的剧情线索”降级。
 */
export function resolveInventoryItemDescription(item: unknown): string {
  if (typeof item === "object" && item) {
    const source = item as Record<string, unknown>;
    const description = textValue(source.description, "");
    if (description && description !== "暂无物品描述。") {
      return description;
    }
    return "已取得的剧情线索。";
  }
  return "已取得的剧情线索。";
}

/**
 * 功能：渲染背包与装备列表，展示后端返回的物品名称和描述。
 * 入参：inventory（unknown[]）：后端角色背包快照，元素可为对象或基础值。
 * 出参：JSX.Element。
 * 异常：不抛异常；无法识别的物品字段降级为内部 ID 或占位文案。
 */
export function InventoryPanel({ inventory }: { inventory: unknown[] }) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PackageIcon data-icon="inline-start" />
          背包 / 装备
        </CardTitle>
        <CardAction>
          <Badge variant="outline">{inventory.length}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent>
        {inventory.length ? (
          <div className="grid grid-cols-1 gap-2">
            {inventory.slice(0, 6).map((item, idx) => (
              <div key={idx} className="rounded-lg border bg-muted/30 p-2 text-sm">
                <div className="font-medium">
                  {resolveInventoryItemName(item)}
                </div>
                <div className="text-xs text-muted-foreground">
                  {resolveInventoryItemDescription(item)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无物品信息</p>
        )}
      </CardContent>
    </Card>
  );
}
