import { createRequestId, requestJson } from "@/api/client";
import { parseSseChunk } from "@/lib/sse";
import { useDebugStore } from "@/stores/debugStore";
import type { StreamEventPayload, TurnListPayload, TurnResult } from "@/types";

export type TurnInput = {
  user_input: string;
  character_id?: string;
  sandbox_mode?: boolean;
};

export type StreamHandlers = {
  onEvent?: (event: string, payload: StreamEventPayload) => void;
};

type StreamDebugThrottle = {
  lastGmDeltaDebugAt: number;
};

/**
 * 功能：按 SSE 事件类型节流写入调试面板，避免 token 级 gm_delta 造成频繁刷新。
 * 入参：event（string）：SSE 事件名；payload（StreamEventPayload）：事件载荷；
 *   throttle（StreamDebugThrottle）：跨事件保留的节流状态。
 * 出参：void。
 * 异常：不显式抛异常；调试 store 写入异常按前端运行时错误暴露。
 */
function recordStreamDebugEvent(
  event: string,
  payload: StreamEventPayload,
  throttle: StreamDebugThrottle
): void {
  const nowMs = Date.now();
  if (event === "gm_delta" && nowMs - throttle.lastGmDeltaDebugAt < 250) {
    return;
  }
  if (event === "gm_delta") {
    throttle.lastGmDeltaDebugAt = nowMs;
  }
  useDebugStore.getState().setLastSseEvent({ event, payload });
}

/**
 * 功能：处理单个已解析 SSE 事件，并在 done 时返回权威回合结果。
 * 入参：event/payload：SSE 事件；handlers（StreamHandlers）：调用方事件回调；
 *   throttle（StreamDebugThrottle）：调试事件节流状态。
 * 出参：TurnResult | null，非 done 事件返回 null。
 * 异常：error 事件或非法 done 载荷会抛出 Error。
 */
function handleStreamEvent(
  event: string,
  payload: StreamEventPayload,
  handlers: StreamHandlers,
  throttle: StreamDebugThrottle
): TurnResult | null {
  recordStreamDebugEvent(event, payload, throttle);
  handlers.onEvent?.(event, payload);
  if (event === "error") {
    throw new Error(readStreamErrorMessage(payload));
  }
  if (event !== "done") {
    return null;
  }
  if (typeof payload !== "object" || payload === null) {
    throw new Error("流式 done 事件格式非法：缺少 JSON 对象载荷");
  }
  return payload as TurnResult;
}

/**
 * 功能：从 SSE error 载荷中读取用户可理解的错误消息。
 * 入参：payload（StreamEventPayload）：error 事件载荷。
 * 出参：string，缺少 message 时返回通用错误。
 * 异常：不抛异常。
 */
function readStreamErrorMessage(payload: StreamEventPayload): string {
  if (typeof payload === "object" && payload && "message" in payload) {
    return String(payload.message);
  }
  return "流式回合返回 error 事件";
}

/**
 * 功能：分页读取指定会话的后端持久化回合历史。
 * 入参：sessionId（string）：会话 ID；page（number，默认 1）：页码；pageSize（number，默认 100）：每页条数。
 * 出参：Promise<TurnListPayload>，包含总数和当前页回合摘要。
 * 异常：接口失败时由 requestJson 抛出 ApiError；调用方负责回滚加载态或提示错误。
 */
export async function listSessionTurns(
  sessionId: string,
  page = 1,
  pageSize = 100
): Promise<TurnListPayload> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return requestJson<TurnListPayload>(
    `/api/sessions/${encodeURIComponent(sessionId)}/turns?${params.toString()}`
  );
}

/**
 * 功能：按页拉取一个会话的完整回合历史，供加载存档时恢复对话记录。
 * 入参：sessionId（string）：会话 ID；pageSize（number，默认 100）：后端允许的最大分页大小。
 * 出参：Promise<TurnListPayload>，items 为从旧到新合并后的完整历史。
 * 异常：任一分页请求失败时抛出 ApiError；不吞掉错误，避免 UI 误以为存档已完整恢复。
 */
export async function listAllSessionTurns(
  sessionId: string,
  pageSize = 100
): Promise<TurnListPayload> {
  const firstPage = await listSessionTurns(sessionId, 1, pageSize);
  const allItems = [...firstPage.items];
  const totalPages = Math.ceil(firstPage.total / firstPage.page_size);
  for (let page = 2; page <= totalPages; page += 1) {
    const nextPage = await listSessionTurns(sessionId, page, firstPage.page_size);
    allItems.push(...nextPage.items);
  }
  return {
    ...firstPage,
    page: 1,
    items: allItems,
  };
}

/**
 * 功能：提交普通回合请求并返回后端权威结果。
 * 入参：sessionId（string）会话 ID；input（TurnInput）玩家输入。
 * 出参：Promise<TurnResult>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function createTurn(
  sessionId: string,
  input: TurnInput
): Promise<TurnResult> {
  return requestJson<TurnResult>(`/api/sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({
      request_id: createRequestId("turn"),
      ...input,
    }),
  });
}

/**
 * 功能：提交流式回合请求并持续回调 SSE 事件，最终返回 done 事件中的权威结果。
 * 入参：sessionId（string）会话 ID；input（TurnInput）玩家输入；handlers（StreamHandlers）事件回调；signal（AbortSignal）取消信号。
 * 出参：Promise<TurnResult>，仅在收到 done 后 resolve。
 * 异常：网络错误、流终止无 done、收到 error 事件时抛出 Error。
 */
export async function createTurnStream(
  sessionId: string,
  input: TurnInput,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<TurnResult> {
  const requestId = createRequestId("stream");
  const response = await fetch(`/api/sessions/${sessionId}/turns/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: requestId,
      ...input,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`流式请求失败: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let donePayload: TurnResult | null = null;
  const debugThrottle: StreamDebugThrottle = { lastGmDeltaDebugAt: 0 };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    const chunkText = decoder.decode(value, { stream: true });
    const parsed = parseSseChunk(buffer, chunkText);
    buffer = parsed.remaining;
    for (const evt of parsed.events) {
      const payload = (evt.data ?? {}) as StreamEventPayload;
      const nextDonePayload = handleStreamEvent(
        evt.event,
        payload,
        handlers,
        debugThrottle
      );
      if (nextDonePayload !== null) {
        donePayload = nextDonePayload;
      }
    }
  }

  if (!donePayload) {
    throw new Error("流式回合结束但未收到 done 事件");
  }
  return donePayload;
}
