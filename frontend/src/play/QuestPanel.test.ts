import { describe, expect, it } from "vitest";
import {
  resolveQuestStageDescription,
  resolveQuestStageTitle,
} from "@/play/QuestPanel";

describe("play/QuestPanel", () => {
  it("完成任务显示完成态和最终阶段说明", () => {
    /**
     * 功能：验证任务卡在 completed 状态下不再显示“当前阶段待推进”。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示完成态任务卡展示回归。
     */
    const quest = {
      title: "找回潮誓",
      status: "completed",
      status_label: "已完成",
      stage_label: "把沉默交给清晨",
      stage_description: "沿退潮露出的石梁抵达晓桥，让潮钟重新响起。",
    };

    expect(resolveQuestStageTitle(quest)).toBe("任务已完成");
    expect(resolveQuestStageDescription(quest)).toContain("最终阶段：把沉默交给清晨");
  });
});
