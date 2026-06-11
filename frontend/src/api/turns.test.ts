import { beforeEach, describe, expect, it, vi } from "vitest";
import { listAllSessionTurns } from "@/api/turns";

describe("api/turns", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("按 session_id 拉取完整分页历史并保持回合顺序", async () => {
    /**
     * 功能：验证续玩加载不会只恢复第一页回合，长会话历史应按 session_id 聚合完整。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示会话对话恢复分页或请求路径回归。
     */
    const fetchedPaths: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        fetchedPaths.push(path);
        const page = new URL(path, "http://localhost").searchParams.get("page");
        const items =
          page === "1"
            ? [
                {
                  session_turn_id: 1,
                  is_valid: true,
                  user_input: "观察周围",
                  final_response: "你看见赤灯。",
                  created_at: "2026-06-11T10:00:00Z",
                },
              ]
            : [
                {
                  session_turn_id: 2,
                  is_valid: true,
                  user_input: "询问船夫任伯",
                  final_response: "任伯压低声音。",
                  created_at: "2026-06-11T10:01:00Z",
                },
              ];
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              ok: true,
              trace_id: `trc_history_${page}`,
              session_id: "sess_history_pages",
              page: Number(page),
              page_size: 1,
              total: 2,
              items,
            }),
        };
      })
    );

    // 分页边界：强制 page_size=1，确保测试能证明第二页也会被续玩加载逻辑读取。
    const history = await listAllSessionTurns("sess_history_pages", 1);

    // 请求顺序是恢复体验的一部分：必须先读第一页获取 total，再按页顺序补齐后续回合。
    expect(fetchedPaths).toEqual([
      "/api/sessions/sess_history_pages/turns?page=1&page_size=1",
      "/api/sessions/sess_history_pages/turns?page=2&page_size=1",
    ]);
    expect(history.items.map((item) => item.user_input)).toEqual([
      "观察周围",
      "询问船夫任伯",
    ]);
    expect(history.total).toBe(2);
  });
});
