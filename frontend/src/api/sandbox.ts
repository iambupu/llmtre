import { createRequestId, requestJson } from "@/api/client";

export type SandboxResponse = {
  session_id: string;
  committed?: boolean;
  discarded?: boolean;
  trace_id?: string;
};

/**
 * 功能：并入当前沙盒状态到主线。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<SandboxResponse>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function commitSandbox(sessionId: string): Promise<SandboxResponse> {
  return requestJson<SandboxResponse>(`/api/sessions/${sessionId}/sandbox/commit`, {
    method: "POST",
    body: JSON.stringify({ request_id: createRequestId("commit") }),
  });
}

/**
 * 功能：丢弃当前沙盒状态并回滚到主线。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<SandboxResponse>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function discardSandbox(sessionId: string): Promise<SandboxResponse> {
  return requestJson<SandboxResponse>(`/api/sessions/${sessionId}/sandbox/discard`, {
    method: "POST",
    body: JSON.stringify({ request_id: createRequestId("discard") }),
  });
}
