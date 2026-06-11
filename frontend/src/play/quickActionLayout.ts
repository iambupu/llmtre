import type { SceneSnapshot, SessionPayload, TurnResult } from "@/types";
import { textValue } from "@/play/displayTextSupport";
import type { SceneQuickActionGroups, SceneQuickActionLayout } from "@/play/actionTypes";

const INTERNAL_ACTION_TYPE_KEYS = new Set([
  "observe",
  "wait",
  "rest",
  "move",
  "talk",
  "attack",
  "use_item",
  "inspect",
  "interact",
  "commit_sandbox",
  "discard_sandbox",
  "custom",
]);

/**
 * 功能：判断候选快捷动作是否可直接展示给玩家。
 * 入参：action（string）：后端返回的快捷动作文本或内部动作类型 key。
 * 出参：boolean，玩家可读且可直接提交的动作返回 true；裸内部 key 返回 false。
 * 异常：不抛异常；空白字符串与大小写变体都按不可展示处理。
 */
export function isRenderableQuickAction(action: string): boolean {
  const normalized = action.trim();
  if (!normalized) {
    return false;
  }
  // 显示边界：move/inspect 这类值是协议动作类型，不是玩家可读行动；目标不明确时也不应提交。
  return !INTERNAL_ACTION_TYPE_KEYS.has(normalized.toLowerCase());
}

/**
 * 功能：清洗快捷动作数组，去掉内部动作类型 key 并保持原始提交文本去重。
 * 入参：actions（unknown[]）：可能来自 quick_actions、layout 或场景 suggested_actions。
 * 出参：string[]，仅包含可展示、可提交的玩家动作文本。
 * 异常：不抛异常；非字符串项会被 textValue 降级为空并过滤。
 */
function sanitizeRenderableQuickActions(actions: unknown[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const action of actions) {
    const actionText = textValue(action, "").trim();
    if (!isRenderableQuickAction(actionText) || seen.has(actionText)) {
      continue;
    }
    seen.add(actionText);
    result.push(actionText);
  }
  return result;
}

/**
 * 功能：从后端场景快照中提取可点击行动，优先使用权威 quick_actions，再补充 affordances 与 suggested_actions。
 * 入参：turnData（TurnResult | null）：最近回合结果；scene（SceneSnapshot | null | undefined）：当前场景。
 * 出参：string[]，去重后的可提交行动文本。
 * 异常：不抛异常；缺失或禁用 affordance 会被过滤。
 */
export function resolveTurnQuickActions(turn: TurnResult): string[] {
  const raw = turn.quick_actions?.length
    ? turn.quick_actions
    : (turn.affordances ?? [])
        .filter((item) => item.enabled)
        .map((item) => item.user_input || item.label);
  return sanitizeRenderableQuickActions(raw ?? []).slice(0, 10);
}

/**
 * 功能：把快捷动作文本归一化为语义键，用于“检查四周/观察周围”等同义动作去重。
 * 入参：action（string）：原始快捷动作文本。
 * 出参：string，语义去重键；无法命中规则时返回原文本去空白后的结果。
 * 异常：不抛异常；所有分支均降级为稳定字符串，避免 UI 去重抛错。
 */
export function normalizeActionSemanticKey(action: string): string {
  const normalized = action.replace(/\s+/g, "").trim();
  if (!normalized) {
    return "";
  }
  // TODO(A1-quick-action-intent): 前端仅做展示兜底；后续改为消费后端 LLM 约束后的 canonical_intent_key，
  // 减少纯规则归一化导致的同义动作漏去重。
  const compact = normalized
    .replace(/一下子|一下儿|一下|一会/g, "")
    .trim();
  if (
    /(检查|观察|查看|看看|环顾|打量|侦查|探查|巡视).*(周围|四周|附近|这里|周遭)/.test(
      compact
    )
  ) {
    return "inspect-surroundings";
  }
  return compact;
}

/**
 * 功能：从场景快照构造“当前场景/临近场景”两组快捷动作，并做语义级去重。
 * 入参：scene（SceneSnapshot | null）：当前场景快照。
 * 出参：SceneQuickActionGroups，按场景分组后的快捷动作集合。
 * 异常：不抛异常；缺失 affordances 时回退到 suggested_actions 与 available_actions。
 */
export function resolveSceneQuickActions(
  turnData: TurnResult | null,
  scene: SceneSnapshot | null
): SceneQuickActionGroups {
  if (!scene) {
    const rawGroups = turnData?.quick_action_groups;
    return {
      current: Array.isArray(rawGroups?.current) ? rawGroups.current : [],
      nearby: Array.isArray(rawGroups?.nearby) ? rawGroups.nearby : [],
    };
  }
  const backendGroups = turnData?.quick_action_groups;
  const currentFromBackend = Array.isArray(backendGroups?.current)
    ? backendGroups.current
    : [];
  const nearbyFromBackend = Array.isArray(backendGroups?.nearby)
    ? backendGroups.nearby
    : [];
  if (currentFromBackend.length || nearbyFromBackend.length) {
    return {
      current: [...new Set(currentFromBackend)].slice(0, 10),
      nearby: [...new Set(nearbyFromBackend)].slice(0, 10),
    };
  }
  const currentLocationId = textValue(scene.current_location?.id, "");
  const buckets: SceneQuickActionGroups = { current: [], nearby: [] };
  const seenCurrent = new Set<string>();
  const seenNearby = new Set<string>();

  for (const affordance of scene.affordances ?? []) {
    if (!affordance.enabled) {
      continue;
    }
    const actionText = textValue(affordance.user_input ?? affordance.label, "").trim();
    if (!isRenderableQuickAction(actionText)) {
      continue;
    }
    const semanticKey = normalizeActionSemanticKey(actionText);
    if (!semanticKey) {
      continue;
    }
    const targetLocationId = textValue(affordance.location_id, "");
    const objectId = textValue(affordance.object_id, "");
    const isNearby =
      objectId.startsWith("exit:") ||
      affordance.action_type === "move" ||
      (targetLocationId && targetLocationId !== currentLocationId);
    if (isNearby) {
      if (!seenNearby.has(semanticKey)) {
        seenNearby.add(semanticKey);
        buckets.nearby.push(actionText);
      }
      continue;
    }
    if (!seenCurrent.has(semanticKey)) {
      seenCurrent.add(semanticKey);
      buckets.current.push(actionText);
    }
  }

  for (const fallbackAction of [
    ...(scene.suggested_actions ?? []),
    ...(scene.available_actions ?? []),
  ]) {
    const actionText = textValue(fallbackAction, "").trim();
    if (!isRenderableQuickAction(actionText)) {
      continue;
    }
    const semanticKey = normalizeActionSemanticKey(actionText);
    if (!semanticKey || seenCurrent.has(semanticKey) || seenNearby.has(semanticKey)) {
      continue;
    }
    seenCurrent.add(semanticKey);
    buckets.current.push(actionText);
  }

  return {
    current: buckets.current.slice(0, 10),
    nearby: buckets.nearby.slice(0, 10),
  };
}

/**
 * 功能：生成场景快捷动作布局（顶部公共动作 + 各地点卡片动作），并执行跨区域语义去重。
 * 入参：turnData（TurnResult | null）：本回合结果；scene（SceneSnapshot | null）：场景快照。
 * 出参：SceneQuickActionLayout，供场景栏与地点卡片渲染。
 * 异常：不抛异常；字段缺失时降级为空布局。
 */
export function resolveSceneQuickActionLayout(
  turnData: TurnResult | null,
  sessionData: SessionPayload | null,
  scene: SceneSnapshot | null
): SceneQuickActionLayout {
  const backendLayout = turnData?.quick_action_layout ?? sessionData?.quick_action_layout;
  const backendCommon = Array.isArray(backendLayout?.common_actions)
    ? sanitizeRenderableQuickActions(backendLayout.common_actions)
    : [];
  const backendObjects =
    backendLayout?.object_actions && typeof backendLayout.object_actions === "object"
      ? backendLayout.object_actions
      : {};
  const objectActions = Object.fromEntries(
    Object.entries(backendObjects)
      .map(([key, value]) => [
        key,
        Array.isArray(value) ? sanitizeRenderableQuickActions(value) : [],
      ])
      .filter(([, value]) => value.length > 0)
  );
  if (backendCommon.length || Object.keys(objectActions).length) {
    const layoutUnmapped = Array.isArray(backendLayout?.diagnostics?.unmapped_actions)
      ? (backendLayout?.diagnostics?.unmapped_actions as unknown[])
          .map((item) => textValue(item, ""))
          .filter(Boolean)
      : [];
    return {
      commonActions: [...new Set(backendCommon)],
      objectActions,
      diagnostics: {
        layoutFallbackUsed: false,
        layoutCommonCount: backendCommon.length,
        layoutObjectKeys: Object.keys(objectActions),
        layoutUnmappedActions: [...new Set(layoutUnmapped)],
      },
    };
  }
  const fallbackGroups = resolveSceneQuickActions(turnData, scene);
  return {
    commonActions: fallbackGroups.current,
    objectActions: {},
    diagnostics: {
      layoutFallbackUsed: true,
      layoutCommonCount: fallbackGroups.current.length,
      layoutObjectKeys: [],
      layoutUnmappedActions: fallbackGroups.current,
    },
  };
}
