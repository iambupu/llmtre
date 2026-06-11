import { describe, expect, it } from "vitest";
import { classifyActionForDisplay, groupActionsForDisplay } from "@/play/actionDisplaySupport";

describe("play/actionDisplaySupport", () => {
  it("会把自然移动文案归入移动分组", () => {
    /**
     * 功能：验证赤灯剧本中不以“前往”开头的出口文案仍显示在移动分组。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示对象卡片会把自然移动动作错误显示为“其他”。
     */
    expect(classifyActionForDisplay("沿赤灯巷进镇")).toBe("move");
    expect(classifyActionForDisplay("穿过后门去旧账房")).toBe("move");
    expect(classifyActionForDisplay("回到静默钟院")).toBe("move");
    expect(classifyActionForDisplay("从前门回渡口")).toBe("move");
    expect(classifyActionForDisplay("带着清晨返回鹭潮渡口")).toBe("move");

    const groups = groupActionsForDisplay(["沿赤灯巷进镇", "检查潮汐告示"], null);

    expect(groups.find((item) => item.key === "move")?.actions[0]?.label).toBe(
      "沿赤灯巷进镇"
    );
    expect(groups.find((item) => item.key === "other")).toBeUndefined();
  });
});
