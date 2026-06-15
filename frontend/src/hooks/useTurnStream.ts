import { useRef } from "react";
import { createTurnStream, type TurnInput } from "@/api/turns";
import { useDebugStore } from "@/stores/debugStore";
import { useStreamStore } from "@/stores/streamStore";
import type { TurnResult } from "@/types";

type StreamTextBuffer = {
  pieces: string[];
  flushTimer: number | null;
  gmDeltaCount: number;
};

type StreamEventContext = {
  event: string;
  payload: unknown;
  buffer: StreamTextBuffer;
  addLog: (message: string) => void;
  setTraceId: (traceId: string) => void;
  setStreamingText: (value: string) => void;
};

/**
 * 功能：重置 GM 增量缓存，并清理尚未触发的刷新定时器。
 * 入参：buffer（StreamTextBuffer）：本次流式请求的文本缓存。
 * 出参：void。
 * 异常：不显式抛异常；浏览器定时器异常按运行时错误暴露。
 */
function resetStreamTextBuffer(buffer: StreamTextBuffer): void {
  if (buffer.flushTimer !== null) {
    window.clearTimeout(buffer.flushTimer);
  }
  buffer.pieces = [];
  buffer.flushTimer = null;
  buffer.gmDeltaCount = 0;
}

/**
 * 功能：把缓存中的 GM 增量写入展示状态，并关闭待执行的定时器。
 * 入参：buffer（StreamTextBuffer）：文本缓存；setStreamingText（function）：Zustand 写入函数。
 * 出参：void。
 * 异常：不显式抛异常；字符串合并或状态写入异常按运行时错误暴露。
 */
function flushStreamTextBuffer(
  buffer: StreamTextBuffer,
  setStreamingText: (value: string) => void
): void {
  if (buffer.flushTimer !== null) {
    window.clearTimeout(buffer.flushTimer);
    buffer.flushTimer = null;
  }
  setStreamingText(buffer.pieces.join(""));
}

/**
 * 功能：安排短延迟刷新，把密集 gm_delta 聚合后再写入 UI 状态。
 * 入参：buffer（StreamTextBuffer）：文本缓存；setStreamingText（function）：Zustand 写入函数。
 * 出参：void。
 * 异常：不显式抛异常；浏览器定时器异常按运行时错误暴露。
 */
function scheduleStreamTextFlush(
  buffer: StreamTextBuffer,
  setStreamingText: (value: string) => void
): void {
  if (buffer.flushTimer !== null) {
    return;
  }
  buffer.flushTimer = window.setTimeout(() => {
    buffer.flushTimer = null;
    setStreamingText(buffer.pieces.join(""));
  }, 50);
}

/**
 * 功能：从 gm_delta 事件载荷中读取文本增量。
 * 入参：payload（unknown）：SSE 事件载荷。
 * 出参：string，缺失或非对象载荷返回空字符串。
 * 异常：不抛异常。
 */
function readGmDelta(payload: unknown): string {
  if (typeof payload !== "object" || payload === null || !("delta" in payload)) {
    return "";
  }
  return String((payload as { delta?: unknown }).delta ?? "");
}

/**
 * 功能：记录非 token 级 SSE 事件日志，并把 gm_delta 追加到文本缓存。
 * 入参：context（StreamEventContext）：事件、调试写入函数和缓存状态。
 * 出参：void。
 * 异常：不显式抛异常；状态写入异常按前端运行时错误暴露。
 */
function handleTurnStreamEvent(context: StreamEventContext): void {
  const { event, payload, buffer, addLog, setTraceId, setStreamingText } = context;
  if (event !== "gm_delta") {
    addLog(`SSE: ${event}`);
  }
  if (event === "gm_delta") {
    const delta = readGmDelta(payload);
    if (delta) {
      buffer.gmDeltaCount += 1;
      buffer.pieces.push(delta);
      scheduleStreamTextFlush(buffer, setStreamingText);
    }
    return;
  }
  logStructuredStreamEvent(event, payload, buffer, addLog, setTraceId, setStreamingText);
}

/**
 * 功能：记录触发器、任务和 done 事件的结构化调试信息。
 * 入参：event/payload：SSE 事件；buffer/addLog/setTraceId/setStreamingText：调试与文本状态。
 * 出参：void。
 * 异常：不显式抛异常；非法 payload 会自然跳过对应日志。
 */
function logStructuredStreamEvent(
  event: string,
  payload: unknown,
  buffer: StreamTextBuffer,
  addLog: (message: string) => void,
  setTraceId: (traceId: string) => void,
  setStreamingText: (value: string) => void
): void {
  if (typeof payload !== "object" || payload === null) {
    return;
  }
  const record = payload as Record<string, unknown>;
  if (event === "trigger_evaluation") {
    const triggerId = record.trigger_id ?? record.trigger_type ?? "?";
    addLog(`SSE trigger ${record.fired ? "触发" : "评估"}: ${String(triggerId)}`);
  }
  if (event === "quest_resolution") {
    addLog(`SSE quest: ${String(record.quest_id ?? "?")} → ${String(record.status ?? "?")}`);
  }
  if (event === "done") {
    flushStreamTextBuffer(buffer, setStreamingText);
    if (buffer.gmDeltaCount > 0) {
      addLog(`SSE gm_delta 累计: ${buffer.gmDeltaCount}`);
    }
    if (record.trace_id) {
      setTraceId(String(record.trace_id));
    }
    logDoneSceneSnapshot(record.scene_snapshot, addLog);
  }
}

/**
 * 功能：从 done 载荷中的场景快照记录位置、可用动作和推荐动作数量。
 * 入参：snapshot（unknown）：scene_snapshot 载荷；addLog（function）：调试日志写入函数。
 * 出参：void。
 * 异常：不抛异常；非法快照直接跳过。
 */
function logDoneSceneSnapshot(snapshot: unknown, addLog: (message: string) => void): void {
  if (typeof snapshot !== "object" || snapshot === null) {
    return;
  }
  const snap = snapshot as Record<string, unknown>;
  const location = snap.current_location;
  const locationName =
    typeof location === "object" && location !== null
      ? ((location as Record<string, unknown>).name ?? (location as Record<string, unknown>).label)
      : null;
  const availableCount = Array.isArray(snap.available_actions) ? snap.available_actions.length : 0;
  const suggestedCount = Array.isArray(snap.suggested_actions) ? snap.suggested_actions.length : 0;
  addLog(`SSE done 场景: ${locationName ?? "未知"} | 可用动作=${availableCount} | 推荐动作=${suggestedCount}`);
}

/**
 * 功能：封装流式回合调用生命周期，统一 busy 状态、SSE 事件日志与终止语义。
 * 入参：无。
 * 出参：对象，含 `run(sessionId, input)` 与 `abort()`。
 * 异常：run 内部会把流式错误继续抛给调用方处理。
 */
export function useTurnStream() {
  const controllerRef = useRef<AbortController | null>(null);
  const streamBufferRef = useRef<StreamTextBuffer>({
    pieces: [],
    flushTimer: null,
    gmDeltaCount: 0,
  });
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
    resetStreamTextBuffer(streamBufferRef.current);
    setBusy(true);
    setStreamingText("");
    addLog("开始流式回合请求");
    try {
      const result = await createTurnStream(
        sessionId,
        input,
        {
          onEvent: (event, payload) => {
            handleTurnStreamEvent({
              event,
              payload,
              buffer: streamBufferRef.current,
              addLog,
              setTraceId,
              setStreamingText,
            });
          },
        },
        controllerRef.current.signal
      );
      return result;
    } finally {
      flushStreamTextBuffer(streamBufferRef.current, setStreamingText);
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
    resetStreamTextBuffer(streamBufferRef.current);
    reset();
    addLog("前端已停止接收流式输出");
  }

  return { run, abort };
}
