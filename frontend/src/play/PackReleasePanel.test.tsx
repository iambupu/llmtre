import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PackReleasePanel } from "@/play/PackReleasePanel";
import type { StoryPackSummary } from "@/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

const redLanternSummary: StoryPackSummary = {
  pack_id: "echoes_under_red_lantern",
  title: "赤灯下的回声",
  version: "0.1.0",
  scenario_id: "default",
  start_scene_id: "ferry_landing",
  start_scene_title: "鹭潮渡口",
  compiled_artifact_hash: "f38306b69e14e026",
  source_background_hash: null,
  scene_count: 6,
  interaction_count: 11,
  quest_count: 1,
  trigger_count: 13,
  asset_count: 3,
  diagnostics: [],
};

/**
 * 功能：在 jsdom 中渲染剧本入口面板，复用默认空回调并允许覆盖关键属性。
 * 入参：props（Partial<React.ComponentProps<typeof PackReleasePanel>>）：测试要覆盖的面板属性。
 * 出参：HTMLDivElement，包含渲染结果的测试容器。
 * 异常：React 渲染异常会直接抛出，由 Vitest 判定失败。
 */
function renderPackReleasePanel(
  props: Partial<ComponentProps<typeof PackReleasePanel>> = {}
): HTMLDivElement {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root?.render(
      <PackReleasePanel
        storyPacks={[redLanternSummary]}
        diagnostics={{}}
        selectedPack={redLanternSummary}
        preview={null}
        importDraft=""
        disabled={false}
        isLoading={false}
        isPreviewLoading={false}
        isImporting={false}
        isDeleting={false}
        onPreview={vi.fn()}
        onImportDraftChange={vi.fn()}
        onImport={vi.fn()}
        onDelete={vi.fn()}
        {...props}
      />
    );
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

describe("PackReleasePanel", () => {
  it("默认玩家视图不暴露剧本包内部标识", () => {
    const host = renderPackReleasePanel();
    const text = host.textContent ?? "";

    expect(text).toContain("赤灯下的回声");
    expect(text).toContain("起点 鹭潮渡口");
    expect(text).toContain("导入或编辑剧本包 JSON");
    expect(text).not.toContain("开发信息");
    expect(text).not.toContain("pack_id");
    expect(text).not.toContain("scenario");
    expect(text).not.toContain("hash");
    expect(text).not.toContain(redLanternSummary.pack_id);
    expect(text).not.toContain(redLanternSummary.compiled_artifact_hash);
  });
});
