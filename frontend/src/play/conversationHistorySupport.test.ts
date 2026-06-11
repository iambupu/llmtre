import { describe, expect, it } from "vitest";
import type { SessionPayload, TurnListPayload } from "@/types";
import {
  buildMessagesFromTurnHistory,
  historyMessageClock,
  sessionResumeQuickActions,
} from "@/play/conversationHistorySupport";

describe("play/conversationHistorySupport", () => {
  it("按后端会话回合历史恢复玩家与旁白消息", () => {
    /**
     * 功能：验证恢复逻辑只依赖后端 turns，并按 session_turn_id 从旧到新还原聊天记录。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示会话历史恢复或排序回归。
     */
    const history: TurnListPayload = {
      session_id: "sess_history01",
      page: 1,
      page_size: 100,
      total: 2,
      items: [
        {
          session_turn_id: 2,
          is_valid: true,
          user_input: "前往旧账房",
          final_response: "你推门进入旧账房。",
          created_at: "2026-06-11T16:32:11Z",
        },
        {
          session_turn_id: 1,
          is_valid: true,
          user_input: "观察周围",
          final_response: "你看见雾里亮着赤灯。",
          created_at: "2026-06-11T16:31:00Z",
        },
      ],
    };

    const messages = buildMessagesFromTurnHistory(history, {
      session_id: "sess_history01",
      quick_actions: ["检查周围", "move", "inspect"],
    } as SessionPayload);

    expect(messages.map((item) => `${item.role}:${item.text}`)).toEqual([
      "player:观察周围",
      "gm:你看见雾里亮着赤灯。",
      "player:前往旧账房",
      "gm:你推门进入旧账房。",
    ]);
    expect(messages[3].quickActions).toEqual(["检查周围"]);
    expect(messages[0].at).toBe("16:31:00");
  });

  it("空历史回退到当前场景开场叙事", () => {
    /**
     * 功能：验证新会话尚无 turns 时仍展示当前场景开场，而不是空白聊天面板。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示空历史加载体验回归。
     */
    const messages = buildMessagesFromTurnHistory(
      {
        session_id: "sess_empty01",
        page: 1,
        page_size: 100,
        total: 0,
        items: [],
      },
      {
        session_id: "sess_empty01",
        scene_snapshot: {
          current_location: {
            name: "鹭潮渡口",
            description: "赤灯在雾里提前亮起。",
          },
        },
      } as SessionPayload
    );

    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("gm");
    expect(messages[0].text).toContain("鹭潮渡口");
  });

  it("过滤恢复时的内部快捷动作 key 并兼容坏时间", () => {
    /**
     * 功能：验证恢复快捷动作不会泄漏协议 key，坏时间字符串也不会导致转换抛错。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示恢复降级边界回归。
     */
    expect(
      sessionResumeQuickActions({
        session_id: "sess_actions01",
        quick_actions: ["move", "询问船夫任伯", "inspect", "询问船夫任伯"],
      } as SessionPayload)
    ).toEqual(["询问船夫任伯"]);
    expect(historyMessageClock("not-a-date")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
