import type { SceneAssetRef } from "@/types";

export type SceneAssetMediaType = "image" | "gif" | "video" | "audio";
export type SceneAssetPlaybackMode = "manual" | "once" | "loop";
export type SceneAssetPreload = "none" | "metadata" | "auto";

export type ResolvedSceneAssetPlayback = {
  mode: SceneAssetPlaybackMode;
  controls: boolean;
  muted: boolean;
  preload: SceneAssetPreload;
  volume: number;
  startTimeSeconds: number;
  endTimeSeconds: number | null;
};

export const DEFAULT_SCENE_ASSET_PLAYBACK: ResolvedSceneAssetPlayback = {
  mode: "manual",
  controls: true,
  muted: false,
  preload: "metadata",
  volume: 1,
  startTimeSeconds: 0,
  endTimeSeconds: null,
};

/**
 * 功能：从不可信字段中读取字符串，避免场景资源展示直接依赖 any。
 * 入参：value（unknown）：候选字段；fallback（string，默认空字符串）：非法值降级文本。
 * 出参：string，字符串原值或 fallback。
 * 异常：不抛异常；非字符串按 fallback 降级。
 */
function textValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/**
 * 功能：从不可信字段中读取有限数值，避免播放窗口被 NaN/Infinity 污染。
 * 入参：value（unknown）：候选字段；fallback（number）：非法值降级数值。
 * 出参：number，有限数值或 fallback。
 * 异常：不抛异常；非有限数值按 fallback 降级。
 */
function finiteNumberValue(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  const parsed = typeof value === "string" && value.trim() ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * 功能：根据媒体声明或文件扩展名推断渲染类型。
 * 入参：asset（Partial<SceneAssetRef>）：后端 asset 映射或临时 URL 物料。
 * 出参：SceneAssetMediaType，无法识别时按 image 降级。
 * 异常：不抛异常；未知 media_type/扩展名安全降级为 image。
 */
export function inferSceneAssetMediaType(asset: Partial<SceneAssetRef>): SceneAssetMediaType {
  const declared = textValue(asset.media_type, "").trim().toLowerCase();
  if (declared === "image" || declared === "gif" || declared === "video" || declared === "audio") {
    return declared;
  }
  const mimeType = textValue(asset.mime_type, "").trim().toLowerCase();
  if (mimeType.startsWith("video/")) {
    return "video";
  }
  if (mimeType.startsWith("audio/")) {
    return "audio";
  }
  if (mimeType === "image/gif") {
    return "gif";
  }
  const src = textValue(asset.src || asset.url || "", "").split("?")[0].toLowerCase();
  if (/\.(mp4|webm|ogg|mov|m4v)$/.test(src)) {
    return "video";
  }
  if (/\.(mp3|wav|flac|m4a|aac|oga)$/.test(src)) {
    return "audio";
  }
  if (/\.gif$/.test(src)) {
    return "gif";
  }
  return "image";
}

/**
 * 功能：规整 Story Pack 媒体播放生命周期策略，缺省保持旧版手动播放行为。
 * 入参：asset（SceneAssetRef）：后端下发的媒体资源。
 * 出参：ResolvedSceneAssetPlayback，包含播放模式、控件、音量、预加载和时间窗口。
 * 异常：不抛异常；非法枚举或时间窗口按保守默认值降级。
 */
export function resolveSceneAssetPlayback(asset: SceneAssetRef): ResolvedSceneAssetPlayback {
  const raw = asset.playback ?? {};
  const declaredMode = textValue(raw.mode, "manual").trim().toLowerCase();
  const mode: SceneAssetPlaybackMode =
    declaredMode === "once" || declaredMode === "loop" ? declaredMode : "manual";
  const declaredPreload = textValue(raw.preload, "metadata").trim().toLowerCase();
  const preload: SceneAssetPreload =
    declaredPreload === "none" || declaredPreload === "auto" ? declaredPreload : "metadata";
  const startTimeSeconds = Math.max(0, finiteNumberValue(raw.start_time_seconds, 0));
  const rawEnd = raw.end_time_seconds;
  const parsedEnd =
    rawEnd === null || rawEnd === undefined
      ? null
      : Math.max(0, finiteNumberValue(rawEnd, 0));
  const endTimeSeconds =
    parsedEnd !== null && parsedEnd > startTimeSeconds ? parsedEnd : null;

  return {
    mode,
    controls: raw.controls ?? true,
    muted: raw.muted ?? false,
    preload,
    volume: Math.min(1, Math.max(0, finiteNumberValue(raw.volume, 1))),
    startTimeSeconds,
    endTimeSeconds,
  };
}
