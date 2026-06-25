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
