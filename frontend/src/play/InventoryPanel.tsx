import { PackageIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  resolveInventoryItemDescription,
  resolveInventoryItemName,
} from "@/play/inventoryPanelSupport";

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
                <div className="font-medium">{resolveInventoryItemName(item)}</div>
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
