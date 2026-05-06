import { useRef } from "react";
import { createTurnStream, type TurnInput } from "@/api/turns";
import { useDebugStore } from "@/stores/debugStore";
import { useStreamStore } from "@/stores/streamStore";
import type { TurnResult } from "@/types";

/**
 * 功能：封装流式回合调用生命周期，统一 busy 状态、SSE 事件日志与终止语义。
 * 入参：无。
 * 出参：对象，含 `run(sessionId, input)` 与 `abort()`。
 * 异常：run 内部会把流式错误继续抛给调用方处理。
 */
export function useTurnStream() {
  const controllerRef = useRef<AbortController | null>(null);
  const setBusy = useStreamStore((s) => s.setBusy);
  const setStreamingText = useStreamStore((s) => s.setStreamingText);
  const reset = useStreamStore((s) => s.reset);
  const addLog = useDebugStore((s) => s.addLog);
  const setTraceId = useDebugStore((s) => s.setTraceId);

  async function run(sessionId: string, input: TurnInput): Promise<TurnResult> {
    controllerRef.current = new AbortController();
    setBusy(true);
    setStreamingText("");
    addLog("开始流式回合请求");
    try {
      const result = await createTurnStream(
        sessionId,
        input,
        {
          onEvent: (event, payload) => {
            addLog(`SSE: ${event}`);
            if (event === "gm_delta") {
              const delta =
                typeof payload === "object" && payload && "delta" in payload
                  ? String(payload.delta ?? "")
                  : "";
              const current = useStreamStore.getState().streamingText;
              setStreamingText(`${current}${delta}`);
            }
            if (event === "done" && typeof payload === "object" && payload) {
              if ("trace_id" in payload && payload.trace_id) {
                setTraceId(String(payload.trace_id));
              }
            }
          },
        },
        controllerRef.current.signal
      );
      return result;
    } finally {
      setBusy(false);
    }
  }

  function abort() {
    if (!controllerRef.current) {
      return;
    }
    controllerRef.current.abort();
    reset();
    addLog("前端已停止接收流式输出");
  }

  return { run, abort };
}
