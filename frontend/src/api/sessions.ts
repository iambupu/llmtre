import { createRequestId, requestJson } from "@/api/client";
import type { SessionPayload } from "@/types";

export type CreateSessionInput = {
  character_id?: string;
  sandbox_mode?: boolean;
  pack_id?: string;
  scenario_id?: string;
};

/**
 * 功能：创建会话并返回后端确认的会话状态。
 * 入参：input（CreateSessionInput）可选角色与沙盒参数。
 * 出参：Promise<SessionPayload>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function createSession(
  input: CreateSessionInput
): Promise<SessionPayload> {
  return requestJson<SessionPayload>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      request_id: createRequestId("create"),
      ...input,
    }),
  });
}

/**
 * 功能：读取指定会话详情。
 * 入参：sessionId（string）会话 ID。
 * 出参：Promise<SessionPayload>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function getSession(sessionId: string): Promise<SessionPayload> {
  return requestJson<SessionPayload>(`/api/sessions/${sessionId}`);
}
