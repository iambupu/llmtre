import { describe, expect, it } from "vitest";
import type { ActiveCharacter } from "@/types";
import { resolveStatusEffects, resolveStatusSummary } from "@/play/characterSupport";

describe("play/characterSupport", () => {
  it("会把角色状态里的内部 flag 标签转成中文", () => {
    /**
     * 功能：验证角色状态栏不会直接显示 red lantern story complete 等内部状态 key。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示状态栏可读化回归。
     */
    const character = {
      status_summary: "moved_recently、red_lantern_story_complete",
      status_effects: [
        {
          key: "moved_recently",
          label: "moved_recently",
          kind: "activity",
          severity: "info",
          description: "移动后状态。",
        },
        {
          key: "red_lantern_story_complete",
          label: "red lantern story complete",
          kind: "flag",
          severity: "info",
          description: "主线完成。",
        },
      ],
    } as ActiveCharacter;

    expect(resolveStatusEffects(character).map((item) => item.label)).toEqual([
      "刚刚移动",
      "赤灯事件完成",
    ]);
    expect(resolveStatusSummary(character)).toBe("刚刚移动、赤灯事件完成");
  });
});
