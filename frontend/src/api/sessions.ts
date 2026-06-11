import { createRequestId, requestJson } from "@/api/client";
import type { DeleteSessionPayload, SessionListPayload, SessionPayload } from "@/types";

type CreateSessionCommonInput = {
  character_id?: string;
  sandbox_mode?: boolean;
};

export type CreateSessionInput =
  | (CreateSessionCommonInput & {
      pack_id: string;
      scenario_id?: string;
      background?: string;
    })
  | (CreateSessionCommonInput & {
      background: string;
      pack_id?: undefined;
      scenario_id?: undefined;
    });

/**
 * 功能：读取后端已保存的最近会话摘要列表。
 * 入参：limit（number，默认 20）：最多返回数量，后端会限制在 1-100。
 * 出参：Promise<SessionListPayload>，包含会话摘要、总数和实际 limit。
 * 异常：接口失败时由 requestJson 抛出 ApiError；调用方负责展示降级状态。
 */
export async function listSessions(limit = 20): Promise<SessionListPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  return requestJson<SessionListPayload>(`/api/sessions?${params.toString()}`);
}

/**
 * 功能：创建会话并返回后端确认的会话状态。
 * 入参：input（CreateSessionInput）可选角色与沙盒参数，且必须包含 pack_id 或 background。
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

/**
 * 功能：删除指定会话及其后端私有数据。
 * 入参：sessionId（string）：目标会话 ID，调用方需先确认这是玩家要删除的保存进度。
 * 出参：Promise<DeleteSessionPayload>，包含删除摘要与各子表影响计数。
 * 异常：接口失败时由 requestJson 抛出 ApiError；调用方负责回滚 UI 选择状态或提示错误。
 */
export async function deleteSession(sessionId: string): Promise<DeleteSessionPayload> {
  return requestJson<DeleteSessionPayload>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}
