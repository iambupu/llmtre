import { createRequestId, requestJson } from "@/api/client";
import type { SessionPayload } from "@/types";

/**
 * 功能：重置会话并返回最新会话状态。
 * 入参：sessionId（string）会话 ID；keepCharacter（boolean）是否保留角色。
 * 出参：Promise<SessionPayload>。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function resetSession(
  sessionId: string,
  keepCharacter = true
): Promise<SessionPayload> {
  return requestJson<SessionPayload>(`/api/sessions/${sessionId}/reset`, {
    method: "POST",
    body: JSON.stringify({
      request_id: createRequestId("reset"),
      keep_character: keepCharacter,
    }),
  });
}
