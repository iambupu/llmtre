import { createRequestId, requestJson } from "@/api/client";
import { parseSseChunk } from "@/lib/sse";
import { useDebugStore } from "@/stores/debugStore";
import type { StreamEventPayload, TurnResult } from "@/types";

export type TurnInput = {
  user_input: string;
  character_id?: string;
  sandbox_mode?: boolean;
};

export type StreamHandlers = {
  onEvent?: (event: string, payload: StreamEventPayload) => void;
};

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
      useDebugStore.getState().setLastSseEvent({
        event: evt.event,
        payload,
      });
      handlers.onEvent?.(evt.event, payload);
      if (evt.event === "error") {
        throw new Error(
          typeof payload === "object" && payload && "message" in payload
            ? String(payload.message)
            : "流式回合返回 error 事件"
        );
      }
      if (evt.event === "done") {
        if (typeof payload !== "object" || payload === null) {
          throw new Error("流式 done 事件格式非法：缺少 JSON 对象载荷");
        }
        donePayload = payload as TurnResult;
      }
    }
  }

  if (!donePayload) {
    throw new Error("流式回合结束但未收到 done 事件");
  }
  return donePayload;
}
