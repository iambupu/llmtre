import { requestJson } from "@/api/client";
import type { StoryPackListPayload } from "@/types";

/**
 * 功能：读取后端已校验通过的本地 Story Pack 列表。
 * 入参：无。
 * 出参：Promise<StoryPackListPayload>，包含合法 pack 摘要与坏包诊断。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function listStoryPacks(): Promise<StoryPackListPayload> {
  return requestJson<StoryPackListPayload>("/api/story-packs");
}
