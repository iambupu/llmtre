import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";

import type { SceneAssetRef } from "@/types";
import { cn } from "@/lib/utils";
import {
  DEFAULT_SCENE_ASSET_PLAYBACK,
  inferSceneAssetMediaType,
  resolveSceneAssetPlayback,
} from "@/components/sceneMediaUtils";

type SceneMediaVariant = "hero" | "thumb" | "avatar";

/**
 * 功能：渲染 Story Pack 多媒体物料，作为场景背景、NPC 头像、物品缩略图或音视频片段。
 * 入参：asset（SceneAssetRef | null）：后端提供的媒体物料；
 *   variant（"hero" | "thumb" | "avatar"）：显示尺寸，avatar 保持 1:1 用于 NPC 头像。
 * 出参：JSX.Element | null，有媒体 URL 时渲染 figure，否则不占位。
 * 异常：不抛异常；浏览器媒体加载失败由原生元素处理，不阻塞游戏交互。
 */
export function SceneMedia({
  asset,
  variant,
}: {
  asset: SceneAssetRef | null;
  variant: SceneMediaVariant;
}): ReactElement | null {
  const mediaElementRef = useRef<HTMLMediaElement | null>(null);
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const isHero = variant === "hero";
  const isAvatar = variant === "avatar";
  const mediaType = asset ? inferSceneAssetMediaType(asset) : "image";
  const playback = asset
    ? resolveSceneAssetPlayback(asset)
    : DEFAULT_SCENE_ASSET_PLAYBACK;
  const mediaClassName = "h-full w-full object-cover";
  const useNativeLoop =
    playback.mode === "loop" &&
    playback.startTimeSeconds === 0 &&
    playback.endTimeSeconds === null;
  const showControls = playback.controls || autoplayBlocked;

  /**
   * 功能：保存当前渲染的音视频元素引用，统一支持 audio/video。
   * 入参：element（HTMLMediaElement | null）：React 挂载或卸载时传入的元素。
   * 出参：void。
   * 异常：不抛异常；空元素仅清理引用。
   */
  function bindMediaElement(element: HTMLMediaElement | null): void {
    mediaElementRef.current = element;
  }

  useEffect(() => {
    setAutoplayBlocked(false);
  }, [asset?.asset_id, asset?.url]);

  useEffect(() => {
    const element = mediaElementRef.current;
    if (!element || (mediaType !== "video" && mediaType !== "audio")) {
      return undefined;
    }
    const startAt = playback.startTimeSeconds;
    const endAt = playback.endTimeSeconds;
    let isCorrectingWindow = false;
    element.volume = playback.volume;
    element.muted = playback.muted;

    /**
     * 功能：按配置起点启动媒体播放，并在浏览器阻止自动播放时切换到可恢复控件。
     * 入参：无，读取当前 effect 内已绑定的媒体元素和播放窗口。
     * 出参：void。
     * 异常：不抛异常；play() 被拒绝时记录阻止状态，交由 UI 展示控件。
     */
    const playFromStart = (): void => {
      if (Number.isFinite(startAt)) {
        element.currentTime = startAt;
      }
      void element
        .play()
        .then(() => setAutoplayBlocked(false))
        .catch(() => setAutoplayBlocked(true));
    };
    /**
     * 功能：将当前播放进度限制在 Story Pack 声明的播放窗口内。
     * 入参：无，读取当前媒体元素、播放模式和起止时间。
     * 出参：boolean，true 表示本次事件已修正播放进度或暂停状态。
     * 异常：不抛异常；重复校正通过 isCorrectingWindow 避免递归触发。
     */
    const clampPlaybackWindow = (): boolean => {
      if (isCorrectingWindow) {
        return false;
      }
      if (Number.isFinite(startAt) && startAt > 0 && element.currentTime < startAt) {
        isCorrectingWindow = true;
        element.currentTime = startAt;
        isCorrectingWindow = false;
        return true;
      }
      if (endAt !== null && element.currentTime >= endAt) {
        if (playback.mode === "loop") {
          playFromStart();
          return true;
        }
        element.pause();
        isCorrectingWindow = true;
        element.currentTime = endAt;
        isCorrectingWindow = false;
        return true;
      }
      return false;
    };
    /**
     * 功能：作为媒体事件监听器执行播放窗口约束。
     * 入参：无，事件对象不参与业务判断。
     * 出参：void。
     * 异常：不抛异常；非法时间窗口已在 resolveSceneAssetPlayback 阶段降级。
     */
    const enforcePlaybackWindow = (): void => {
      clampPlaybackWindow();
    };
    /**
     * 功能：在非原生 loop 场景下处理片段循环播放。
     * 入参：无，读取当前播放模式和原生 loop 判定。
     * 出参：void。
     * 异常：不抛异常；播放被浏览器拒绝时复用 playFromStart 的降级路径。
     */
    const handleEnded = (): void => {
      if (playback.mode === "loop" && !useNativeLoop) {
        playFromStart();
      }
    };

    element.addEventListener("loadedmetadata", enforcePlaybackWindow);
    element.addEventListener("timeupdate", enforcePlaybackWindow);
    element.addEventListener("seeking", enforcePlaybackWindow);
    element.addEventListener("ended", handleEnded);
    enforcePlaybackWindow();
    if (playback.mode === "once" || playback.mode === "loop") {
      if (element.readyState >= 1) {
        playFromStart();
      } else {
        element.addEventListener("loadedmetadata", playFromStart, { once: true });
      }
    }

    return () => {
      element.removeEventListener("loadedmetadata", enforcePlaybackWindow);
      element.removeEventListener("loadedmetadata", playFromStart);
      element.removeEventListener("timeupdate", enforcePlaybackWindow);
      element.removeEventListener("seeking", enforcePlaybackWindow);
      element.removeEventListener("ended", handleEnded);
      element.pause();
    };
  }, [
    asset?.asset_id,
    asset?.url,
    mediaType,
    playback.endTimeSeconds,
    playback.mode,
    playback.muted,
    playback.preload,
    playback.startTimeSeconds,
    playback.volume,
    useNativeLoop,
  ]);

  if (!asset?.url) {
    return null;
  }

  return (
    <figure
      className={cn(
        "m-0 overflow-hidden rounded-md border border-primary/20 bg-muted/30",
        isAvatar ? "aspect-square w-24 shrink-0" : "",
        mediaType === "audio" ? "flex items-center p-3" : "",
        isHero ? "h-52" : isAvatar ? "" : "mb-3 h-28"
      )}
    >
      {mediaType === "video" ? (
        <video
          ref={bindMediaElement}
          src={asset.url}
          className={mediaClassName}
          controls={showControls}
          loop={useNativeLoop}
          muted={playback.muted}
          playsInline
          preload={playback.preload}
        />
      ) : mediaType === "audio" ? (
        <audio
          ref={bindMediaElement}
          src={asset.url}
          className="w-full"
          controls={showControls}
          loop={useNativeLoop}
          muted={playback.muted}
          preload={playback.preload}
        />
      ) : (
        <img
          src={asset.url}
          alt={asset.alt || asset.caption || "剧本媒体"}
          className={mediaClassName}
          loading="lazy"
        />
      )}
      {asset.caption && isHero ? (
        <figcaption className="sr-only">{asset.caption}</figcaption>
      ) : null}
    </figure>
  );
}
