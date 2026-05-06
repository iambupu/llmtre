import { createRequestId, requestJson } from "@/api/client";

export type MemoryResponse = {
  session_id: string;
  summary?: string;
  text?: string;
};

/**
 * 功能：读取会话记忆摘要。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<MemoryResponse>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function getMemory(sessionId: string): Promise<MemoryResponse> {
  return requestJson<MemoryResponse>(`/api/sessions/${sessionId}/memory?format=summary`);
}

/**
 * 功能：触发会话记忆刷新。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<MemoryResponse>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function refreshMemory(sessionId: string): Promise<MemoryResponse> {
  return requestJson<MemoryResponse>(`/api/sessions/${sessionId}/memory/refresh`, {
    method: "POST",
    body: JSON.stringify({
      request_id: createRequestId("mem"),
      max_turns: 20,
    }),
  });
}
