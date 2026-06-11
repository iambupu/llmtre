import { describe, expect, it } from "vitest";
import { isQuestCompleted, resolvePlayerGoal } from "@/play/sceneGoalSupport";

describe("play/sceneGoalSupport", () => {
  it("优先使用任务当前阶段作为玩家目标", () => {
    /**
     * 功能：验证当前目标不退回总任务标题，确保玩家看到的是下一步阶段。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示任务目标展示优先级回归。
     */
    const goal = resolvePlayerGoal(
      [
        {
          title: "找回潮誓",
          stage_label: "比对证词与账册",
          stage_description: "从证词和旧账册中找出矛盾。",
        },
      ],
      null
    );

    expect(goal).toBe("比对证词与账册");
  });

  it("任务完成后当前目标显示完成态", () => {
    /**
     * 功能：验证完成态任务不再继续显示阶段推进目标。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示完成态目标展示回归。
     */
    const quest = {
      title: "找回潮誓",
      status: "completed",
      status_label: "已完成",
      stage_label: "把沉默交给清晨",
    };

    expect(isQuestCompleted(quest)).toBe(true);
    expect(resolvePlayerGoal([quest], null)).toBe("已完成：找回潮誓");
  });
});
