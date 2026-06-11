import type { SceneObjectRef, SceneSnapshot } from "@/types";
import type { SceneQuickActionLayout } from "@/play/actionSupport";
import { normalizeActionSemanticKey } from "@/play/actionSupport";
import { textValue } from "@/play/displayTextSupport";

/**
 * 功能：解析对象卡片可提交动作，优先使用后端 quick_action_layout，缺失时回退到同 object_id 的 affordance。
 * 入参：item（SceneObjectRef）：当前对象；sceneQuickActionLayout（SceneQuickActionLayout）：后端动作布局；
 *   scene（SceneSnapshot | null）：当前场景快照。
 * 出参：string[]，仅包含后端已经返回的动作文本，按语义去重。
 * 异常：不抛异常；字段缺失时返回空数组。
 */
export function resolveObjectActionsForDisplay(
  item: SceneObjectRef,
  sceneQuickActionLayout: SceneQuickActionLayout,
  scene: SceneSnapshot | null
): string[] {
  const actions = [...(sceneQuickActionLayout.objectActions[item.object_id] ?? [])];
  for (const affordance of scene?.affordances ?? []) {
    if (!affordance.enabled || affordance.object_id !== item.object_id) {
      continue;
    }
    const actionText = textValue(affordance.user_input ?? affordance.label, "").trim();
    if (actionText) {
      actions.push(actionText);
    }
  }
  const seen = new Set<string>();
  const deduped: string[] = [];
  for (const action of actions) {
    const semanticKey = normalizeActionSemanticKey(action);
    if (!semanticKey || seen.has(semanticKey)) {
      continue;
    }
    seen.add(semanticKey);
    deduped.push(action);
  }
  return deduped;
}
