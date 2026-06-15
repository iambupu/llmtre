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

  it("最近回合事实展示后端分支后果摘要", () => {
    /**
     * 功能：验证选择后果展示来自 branch_consequences 结构化字段。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示前端仍未展示 A3 分支后果。
     */
    const facts = resolveTurnFacts({
      branch_consequences: [
        {
          branch_path: "report_to_watch",
          state_changes: [{ kind: "quest_stage" }, { kind: "state_flag" }],
        },
      ],
    } as TurnResult);

    expect(facts).toContain("选择后果：report_to_watch（2 项）");
  });
});
