import { describe, expect, it, vi, beforeEach } from "vitest";
import { ApiError, createRequestId, requestJson } from "@/api/client";
import { useDebugStore } from "@/stores/debugStore";

describe("api/client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useDebugStore.setState({
      traceId: "",
      lastRequest: null,
      lastSseEvent: null,
      logs: [],
    });
  });

  it("能注入并返回成功 JSON，同时记录 trace_id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            ok: true,
            trace_id: "trc_123",
            value: 7,
          }),
      }))
    );
    const result = await requestJson<{ value: number }>("/api/demo");
    expect(result.value).toBe(7);
    expect(useDebugStore.getState().traceId).toBe("trc_123");
    expect(useDebugStore.getState().lastRequest).toBeTruthy();
  });

  it("ok=false 时抛 ApiError 并带 trace_id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            ok: false,
            trace_id: "trc_err",
            error: { code: "BAD", message: "bad request" },
          }),
      }))
    );
    await expect(requestJson("/api/demo")).rejects.toBeInstanceOf(ApiError);
    await requestJson("/api/demo").catch((err) => {
      const apiErr = err as ApiError;
      expect(apiErr.traceId).toBe("trc_err");
      expect(apiErr.code).toBe("BAD");
    });
  });

  it("createRequestId 生成带作用域前缀的幂等键", () => {
    const id = createRequestId("turn");
    expect(id.startsWith("reqturn_")).toBe(true);
  });
});
