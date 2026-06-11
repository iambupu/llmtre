import { requestJson } from "@/api/client";
import type {
  StoryPackDeletePayload,
  StoryPackDetailPayload,
  StoryPackImportPayload,
  StoryPackImportResult,
  StoryPackListPayload,
} from "@/types";

/**
 * 功能：读取后端已校验通过的本地 Story Pack 列表。
 * 入参：无。
 * 出参：Promise<StoryPackListPayload>，包含合法 pack 摘要与坏包诊断。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function listStoryPacks(): Promise<StoryPackListPayload> {
  return requestJson<StoryPackListPayload>("/api/story-packs");
}

/**
 * 功能：读取单个 Story Pack 的 manifest 与场景预览。
 * 入参：packId（string）：已通过 registry 校验的 pack_id。
 * 出参：Promise<StoryPackDetailPayload>，包含 summary、manifest、scenes。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function getStoryPack(packId: string): Promise<StoryPackDetailPayload> {
  return requestJson<StoryPackDetailPayload>(`/api/story-packs/${encodeURIComponent(packId)}`);
}

/**
 * 功能：上传 A2-Release Story Pack JSON 文件集合。
 * 入参：payload（StoryPackImportPayload）：manifest/scenes/lore/quests/triggers 集合。
 * 出参：Promise<StoryPackImportResult>，包含导入后 summary。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function importStoryPack(
  payload: StoryPackImportPayload
): Promise<StoryPackImportResult> {
  return requestJson<StoryPackImportResult>("/api/story-packs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * 功能：删除本地非官方 Story Pack。
 * 入参：packId（string）：目标 pack_id。
 * 出参：Promise<StoryPackDeletePayload>，包含 deleted_pack_id。
 * 异常：接口失败时由 requestJson 抛出 ApiError。
 */
export async function deleteStoryPack(packId: string): Promise<StoryPackDeletePayload> {
  return requestJson<StoryPackDeletePayload>(`/api/story-packs/${encodeURIComponent(packId)}`, {
    method: "DELETE",
  });
}
