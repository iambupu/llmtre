import { createRequestId, requestJson } from "@/api/client";

export type SandboxResponse = {
  session_id: string;
  committed?: boolean;
  discarded?: boolean;
  trace_id?: string;
  sandbox_diff?: unknown;
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

/**
 * 功能：预览当前沙盒与主线状态差异，不推进回合。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<SandboxResponse>，sandbox_diff 包含后端结构化差异或 diagnostics。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function previewSandboxDiff(sessionId: string): Promise<SandboxResponse> {
  return requestJson<SandboxResponse>(`/api/sessions/${sessionId}/sandbox/diff`);
}
