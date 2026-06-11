import { describe, expect, it } from "vitest";
import type { SceneObjectRef } from "@/types";
import {
  resolveSceneObjectDescription,
  resolveSceneObjectTypeLabel,
  rewriteActionLabelForDisplay,
} from "@/play/sceneDisplaySupport";

describe("play/sceneDisplaySupport", () => {
  it("会把后端对象类型枚举转成中文标签", () => {
    expect(resolveSceneObjectTypeLabel("location")).toBe("地点");
    expect(resolveSceneObjectTypeLabel("exit")).toBe("出口");
    expect(resolveSceneObjectTypeLabel("npc")).toBe("角色");
  });

  it("会过滤对象描述里的 location/exit 内部标记", () => {
    const locationObject: SceneObjectRef = {
      object_id: "location:ferry_landing",
      object_type: "location",
      label: "鹭潮渡口",
      description: "location",
    };
    const exitObject: SceneObjectRef = {
      object_id: "exit:red_lantern_lane",
      object_type: "exit",
      label: "沿赤灯巷进镇",
      description: "exit",
    };

    expect(resolveSceneObjectDescription(locationObject, new Map())).toBe(
      "当前场景中的地点对象。"
    );
    expect(resolveSceneObjectDescription(exitObject, new Map())).toBe(
      "可移动到其他地点的出口。"
    );
  });

  it("保留剧本提供的中文描述", () => {
    const object: SceneObjectRef = {
      object_id: "interaction:tide_notice",
      object_type: "interaction",
      label: "潮汐告示",
      description: "盐霜卷边的潮汐告示。",
    };

    expect(resolveSceneObjectDescription(object, new Map())).toBe("盐霜卷边的潮汐告示。");
  });

  it("会把玩家可见文本里的状态 flag 转成中文标签", () => {
    /**
     * 功能：验证聊天记录和记忆摘要中的内部状态 key 不直接泄漏到玩家主界面。
     * 入参：无。
     * 出参：None；通过断言表达结果。
     * 异常：断言失败表示状态 key 展示映射回归。
     */
    const text = rewriteActionLabelForDisplay(
      "状态标记：inspected_surroundings、scribe_yan_missing_page、tide_oath_shard_recovered、red_lantern_story_complete。",
      new Map()
    );

    expect(text).toBe(
      "状态标记：仔细检查、燕书吏证实缺页、找回潮誓碎片、赤灯事件完成。"
    );
    expect(text).not.toContain("inspected");
    expect(text).not.toContain("scribe_yan");
    expect(text).not.toContain("red_lantern");
  });
});
