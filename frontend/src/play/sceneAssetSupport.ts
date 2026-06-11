import { inferSceneAssetMediaType } from "@/components/sceneMediaUtils";
import type { SceneAssetRef, SceneSnapshot } from "@/types";
import { textValue } from "@/play/displayTextSupport";

/**
 * 功能：从场景对象或 source_ref 中解析多媒体物料，优先使用后端注入的 URL，再回查 scene.assets。
 * 入参：scene（SceneSnapshot | null）：当前场景快照；source（Record<string, unknown>）：对象字段；
 *   idFields（string[]）：可引用 asset_id 的字段；urlFields（string[]）：可直接提供 URL 的字段。
 * 出参：SceneAssetRef | null，命中返回可渲染媒体物料，否则返回 null。
 * 异常：不抛异常；字段缺失或 URL 为空时安全降级为 null。
 */
export function resolveSceneAsset(
  scene: SceneSnapshot | null,
  source: Record<string, unknown>,
  idFields: string[],
  urlFields: string[]
): SceneAssetRef | null {
  for (const field of urlFields) {
    const url = textValue(source[field], "").trim();
    if (url) {
      const mediaType = inferSceneAssetMediaType({ src: url, url });
      return {
        asset_id: field,
        kind: "inline",
        media_type: mediaType,
        src: url,
        url,
        alt: textValue(source.label ?? source.name, "剧本媒体"),
      };
    }
  }
  for (const field of idFields) {
    const assetId = textValue(source[field], "").trim();
    const asset = assetId ? scene?.assets?.[assetId] : null;
    if (asset?.url) {
      return asset;
    }
  }
  return null;
}
