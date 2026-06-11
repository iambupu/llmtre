import {
  CompassIcon,
  MapIcon,
  MessageSquareTextIcon,
  PackageIcon,
  SearchIcon,
  SwordsIcon,
  WandSparklesIcon,
} from "lucide-react";
import type { SceneSnapshot } from "@/types";
import { textValue } from "@/play/displayTextSupport";
import type {
  ActionButtonChoice,
  ActionCategoryGroup,
  ActionCategoryKey,
} from "@/play/actionTypes";
import type { SceneDisplayLabelResolver } from "@/play/sceneSupport";
import { rewriteActionLabelForDisplay } from "@/play/sceneSupport";

/**
 * 功能：根据后端 affordance 的 action_type 或动作文本，把快捷行动分组展示。
 * 入参：actions（string[]）：后端返回的可提交动作文本；scene（SceneSnapshot | null）：当前场景快照；
 *   displayResolver（SceneDisplayLabelResolver）：动作标签中的系统 ID 显示名映射。
 * 出参：ActionCategoryGroup[]，仅包含有动作的分组，前端只改变展示标签，不改变提交内容。
 * 异常：不抛异常；无法识别的动作归入“其他”。
 */
export function groupActionsForDisplay(
  actions: string[],
  scene: SceneSnapshot | null,
  displayResolver: SceneDisplayLabelResolver = new Map()
): ActionCategoryGroup[] {
  const actionTypeByText = new Map<string, string>();
  for (const affordance of scene?.affordances ?? []) {
    const text = textValue(affordance.user_input ?? affordance.label, "").trim();
    if (text) {
      actionTypeByText.set(text, affordance.action_type);
    }
  }
  const buckets: Record<ActionCategoryKey, ActionButtonChoice[]> = {
    observe: [],
    inspect: [],
    talk: [],
    move: [],
    use_item: [],
    attack: [],
    other: [],
  };
  for (const action of actions) {
    const category = classifyActionForDisplay(action, actionTypeByText.get(action));
    if (!buckets[category].some((item) => item.value === action)) {
      buckets[category].push({
        value: action,
        label: rewriteActionLabelForDisplay(action, displayResolver),
      });
    }
  }
  const metadata: Array<Omit<ActionCategoryGroup, "actions">> = [
    { key: "observe", label: "观察", icon: CompassIcon },
    { key: "inspect", label: "检查", icon: SearchIcon },
    { key: "talk", label: "交谈", icon: MessageSquareTextIcon },
    { key: "move", label: "移动", icon: MapIcon },
    { key: "use_item", label: "使用", icon: PackageIcon },
    { key: "attack", label: "战斗", icon: SwordsIcon },
    { key: "other", label: "其他", icon: WandSparklesIcon },
  ];
  return metadata
    .map((item) => ({ ...item, actions: buckets[item.key] }))
    .filter((item) => item.actions.length > 0);
}

/**
 * 功能：把动作文本粗略归入显示类别，作为后端 action_type 缺失时的视觉兜底。
 * 入参：action（string）：动作文本；backendType（string | undefined）：后端 affordance.action_type。
 * 出参：ActionCategoryKey，供按钮分组使用。
 * 异常：不抛异常；未知动作归入 other。
 */
export function classifyActionForDisplay(
  action: string,
  backendType?: string
): ActionCategoryKey {
  const normalizedBackend = (backendType ?? "").trim();
  if (
    normalizedBackend === "observe" ||
    normalizedBackend === "inspect" ||
    normalizedBackend === "talk" ||
    normalizedBackend === "move" ||
    normalizedBackend === "use_item" ||
    normalizedBackend === "attack"
  ) {
    return normalizedBackend;
  }
  const compact = action.replace(/\s+/g, "");
  if (/观察|环顾|看看|打量|侦查/.test(compact)) {
    return "observe";
  }
  if (/检查|查看|搜索|翻找|研究/.test(compact)) {
    return "inspect";
  }
  if (/交谈|询问|呼唤|对话|问/.test(compact)) {
    return "talk";
  }
  if (
    /前往|进入|走向|返回|回到|移动|离开|穿过|去往|沿.+(?:走|去|进|到)|从.+回|带着.+返回/.test(
      compact
    )
  ) {
    return "move";
  }
  if (/使用|打开|点燃|喝下|装备/.test(compact)) {
    return "use_item";
  }
  if (/攻击|战斗|射击|挥砍/.test(compact)) {
    return "attack";
  }
  return "other";
}
