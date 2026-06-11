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

  /**
   * 功能：发起一次 SSE 回合请求，并把增量叙事、trace 和阶段日志写入前端状态。
   * 入参：sessionId（string）：当前 Web 会话 ID；input（TurnInput）：回合请求体。
   * 出参：Promise<TurnResult>，在收到 done 事件后解析为最终回合结果。
   * 异常：网络错误、SSE error 或解析失败会继续抛出；finally 中保证 busy 状态释放。
   */
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
            if (event === "trigger_evaluation" && typeof payload === "object" && payload) {
              const te = payload as Record<string, unknown>;
              const tid = te.trigger_id ?? te.trigger_type ?? "?";
              const fired = te.fired ? "触发" : "评估";
              addLog(`SSE trigger ${fired}: ${String(tid)}`);
            }
            if (event === "quest_resolution" && typeof payload === "object" && payload) {
              const qr = payload as Record<string, unknown>;
              const qid = qr.quest_id ?? "?";
              const qst = qr.status ?? "?";
              addLog(`SSE quest: ${String(qid)} → ${String(qst)}`);
            }
            if (event === "done" && typeof payload === "object" && payload) {
              if ("trace_id" in payload && payload.trace_id) {
                setTraceId(String(payload.trace_id));
              }
              if ("scene_snapshot" in payload && payload.scene_snapshot && typeof payload.scene_snapshot === "object") {
                const snap = payload.scene_snapshot as Record<string, unknown>;
                const loc = snap.current_location;
                const locName = typeof loc === "object" && loc && loc !== null ? (loc as Record<string, unknown>).name ?? (loc as Record<string, unknown>).label : null;
                const avail = Array.isArray(snap.available_actions) ? snap.available_actions.length : 0;
                const sugg = Array.isArray(snap.suggested_actions) ? snap.suggested_actions.length : 0;
                addLog(`SSE done 场景: ${locName ?? "未知"} | 可用动作=${avail} | 推荐动作=${sugg}`);
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

  /**
   * 功能：中止当前前端持有的流式回合请求并清理临时输出态。
   * 入参：无，读取 controllerRef 中最近一次请求的 AbortController。
   * 出参：void。
   * 异常：不显式抛异常；没有活动请求时直接返回。
   */
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
