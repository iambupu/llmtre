import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SceneMedia } from "@/components/SceneMedia";
import type { SceneAssetRef } from "@/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

/**
 * 功能：在 jsdom 容器中渲染 SceneMedia，供媒体生命周期测试复用。
 * 入参：asset（SceneAssetRef）：待渲染的剧本媒体资源；
 *   variant（"hero" | "thumb" | "avatar"）：目标媒体展示尺寸，默认覆盖 hero 场景。
 * 出参：HTMLDivElement，包含渲染结果的测试容器。
 * 异常：React 渲染异常会直接抛出，由 Vitest 判定失败。
 */
function renderSceneMedia(
  asset: SceneAssetRef,
  variant: "hero" | "thumb" | "avatar" = "hero"
): HTMLDivElement {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root?.render(<SceneMedia asset={asset} variant={variant} />);
  });
  return container;
}

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  container = null;
  vi.restoreAllMocks();
});

describe("SceneMedia", () => {
  it("头像变体保持 1:1 比例，避免 NPC 肖像被拉成横向缩略图", () => {
    const host = renderSceneMedia(
      {
        asset_id: "ren_bo_portrait",
        kind: "portrait",
        media_type: "image",
        src: "ren-bo.png",
        url: "/media/ren-bo.png",
      },
      "avatar"
    );
    const figure = host.querySelector("figure");

    expect(figure).not.toBeNull();
    expect(figure?.className).toContain("aspect-square");
    expect(figure?.className).toContain("w-24");
  });

  it("自动播放被拒绝时会显示原生控件，避免玩家无恢复入口", async () => {
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(() =>
      Promise.reject(new DOMException("blocked", "NotAllowedError"))
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);

    const host = renderSceneMedia({
      asset_id: "intro",
      kind: "illustration",
      media_type: "video",
      src: "intro.mp4",
      url: "/media/intro.mp4",
      playback: {
        mode: "once",
        controls: false,
        muted: false,
      },
    });
    const video = host.querySelector("video");

    expect(video).not.toBeNull();
    expect(video?.controls).toBe(false);

    await act(async () => {
      video?.dispatchEvent(new Event("loadedmetadata"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(video?.controls).toBe(true);
  });

  it("会同时限制播放窗口的起点和终点", async () => {
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockImplementation(() =>
      Promise.resolve()
    );
    vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);

    const host = renderSceneMedia({
      asset_id: "loop",
      kind: "illustration",
      media_type: "video",
      src: "loop.mp4",
      url: "/media/loop.mp4",
      playback: {
        mode: "manual",
        controls: true,
        start_time_seconds: 3,
        end_time_seconds: 8,
      },
    });
    const video = host.querySelector("video");

    expect(video).not.toBeNull();
    if (!video) {
      return;
    }

    await act(async () => {
      video.currentTime = 1;
      video.dispatchEvent(new Event("seeking"));
    });
    expect(video.currentTime).toBe(3);

    await act(async () => {
      video.currentTime = 9;
      video.dispatchEvent(new Event("timeupdate"));
    });
    expect(video.currentTime).toBe(8);
  });
});
