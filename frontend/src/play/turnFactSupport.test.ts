import { describe, expect, it } from "vitest";
import type { TurnResult } from "@/types";
import { resolveTurnFacts, resolveTurnOutcomeLabel } from "@/play/turnFactSupport";

describe("play/turnFactSupport", () => {
  it("会把后端 outcome 枚举转换为中文结果", () => {
    /**
     * 功能：验证玩家主界面的最近结果徽标不直接显示 valid_action 等内部枚举。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示 outcome 展示映射回归。
     */
    expect(resolveTurnOutcomeLabel("valid_action")).toBe("行动成立");
    expect(resolveTurnOutcomeLabel("invalid_action")).toBe("行动未成立");
    expect(resolveTurnOutcomeLabel("unknown_outcome")).toBe("系统结果");
  });

  it("最近回合事实使用中文 outcome 标签", () => {
    /**
     * 功能：验证 resolveTurnFacts 只输出玩家可读结果文案。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示最近结果摘要仍泄漏内部枚举。
     */
    const facts = resolveTurnFacts({
      outcome: "valid_action",
      active_character: {
        status_summary: "状态稳定",
      },
    } as TurnResult);

    expect(facts).toContain("状态：状态稳定");
    expect(facts).toContain("结果：行动成立");
    expect(facts.join(" ")).not.toContain("valid_action");
  });
});
