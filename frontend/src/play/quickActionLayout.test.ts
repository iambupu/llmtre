import { describe, expect, it } from "vitest";
import type { SessionPayload, TurnResult } from "@/types";
import {
  isRenderableQuickAction,
  resolveSceneQuickActionLayout,
  resolveTurnQuickActions,
} from "@/play/quickActionLayout";

describe("play/quickActionLayout", () => {
  it("会过滤裸内部动作类型 key，避免 move/inspect 泄漏到玩家按钮", () => {
    const sessionData = {
      session_id: "sess_ui_action_key",
      quick_action_layout: {
        common_actions: ["等待片刻", "短暂休息", "move", "inspect"],
        object_actions: {},
      },
    } as SessionPayload;

    const layout = resolveSceneQuickActionLayout(null, sessionData, null);

    expect(layout.commonActions).toEqual(["等待片刻", "短暂休息"]);
    expect(layout.commonActions).not.toContain("move");
    expect(layout.commonActions).not.toContain("inspect");
  });

  it("回合快捷动作同样只保留玩家可读动作文本", () => {
    const turn = {
      session_id: "sess_ui_action_turn",
      session_turn_id: 1,
      runtime_turn_id: 1,
      final_response: "继续推进。",
      quick_actions: ["move", "观察周围", "inspect", "询问船夫任伯"],
    } as TurnResult;

    expect(resolveTurnQuickActions(turn)).toEqual(["观察周围", "询问船夫任伯"]);
    expect(isRenderableQuickAction("move")).toBe(false);
    expect(isRenderableQuickAction("检查潮汐告示")).toBe(true);
  });
});
