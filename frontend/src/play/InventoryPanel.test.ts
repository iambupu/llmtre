import { describe, expect, it } from "vitest";
import {
  resolveInventoryItemDescription,
  resolveInventoryItemName,
} from "@/play/InventoryPanel";

describe("play/InventoryPanel", () => {
  it("会把赤灯剧本物品 ID 转成玩家可读名称", () => {
    /**
     * 功能：验证背包不会直接展示 tide_oath_shard 等剧情物品内部 ID。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示背包物品可读化回归。
     */
    const item = {
      item_id: "tide_oath_shard",
      name: "tide_oath_shard",
      description: "暂无物品描述。",
    };

    expect(resolveInventoryItemName(item)).toBe("潮誓碎片");
    expect(resolveInventoryItemDescription(item)).toBe("已取得的剧情线索。");
    expect(resolveInventoryItemName("ledger_rubbing")).toBe("潮税账册拓片");
  });
});
