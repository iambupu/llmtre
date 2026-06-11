import type { SceneObjectRef, SceneSnapshot } from "@/types";
import { textValue } from "@/play/displayTextSupport";

export type SceneDisplayLabelResolver = Map<string, string>;

const SCENE_OBJECT_TYPE_LABELS: Record<string, string> = {
  interaction: "互动",
  npc: "角色",
  location: "地点",
  exit: "出口",
  system: "系统",
};

const SCENE_OBJECT_DESCRIPTION_FALLBACKS: Record<string, string> = {
  interaction: "可调查或互动的场景线索。",
  npc: "可交谈或互动的角色。",
  location: "当前场景中的地点对象。",
  exit: "可移动到其他地点的出口。",
  system: "场景运行提示。",
};

const SYSTEM_IDENTIFIER_LABELS: Record<string, string> = {
  moved_recently: "刚刚移动",
  conversation_started: "开始交谈",
  observed_surroundings: "警觉观察",
  inspected_surroundings: "仔细检查",
  waited_recently: "短暂停留",
  rested_recently: "短暂休整",
  sandbox_merged: "沙盒已并入",
  sandbox_discarded: "沙盒已回滚",
  red_lantern_case_started: "赤灯事件展开",
  notice_scraped_name_found: "发现潮汐告示线索",
  boatman_heard_wrong_bell: "听到错钟证词",
  scribe_yan_missing_page: "燕书吏证实缺页",
  ledger_second_boat_found: "发现第二艘船线索",
  lantern_keeper_knows_oath: "得知潮誓传闻",
  three_knot_order_seen: "看见三结顺序",
  stone_mender_revealed_stairs: "发现钟后石阶",
  silent_bell_unsealed: "静默潮钟已解封",
  salt_lock_maintained: "盐锁仍在维持",
  tide_oath_shard_recovered: "找回潮誓碎片",
  dawn_bridge_seen: "看见晓桥",
  red_lantern_story_complete: "赤灯事件完成",
  salt_stained_notice: "盐霜潮汐告示",
  bell_clapper_thread: "钟舌红线",
  ledger_rubbing: "潮税账册拓片",
  tide_oath_shard: "潮誓碎片",
};

/**
 * 功能：把系统标识符归一化成映射表键，兼容下划线、短横线、空格和命名空间前缀。
 * 入参：value（string）：可能来自状态 flag、触发器 ID 或其他后端 key。
 * 出参：string，统一小写并以下划线分隔的内部查找键。
 * 异常：不抛异常；空白输入返回空字符串。
 */
function normalizeSystemIdentifierKey(value: string): string {
  return value
    .trim()
    .replace(/^[a-z][a-z0-9]*:/i, "")
    .replace(/[\s-]+/g, "_")
    .toLowerCase();
}

/**
 * 功能：判断场景对象描述是否只是内部对象类型标记。
 * 入参：value（string）：对象描述或类型文本；objectType（string）：后端 object_type。
 * 出参：boolean，true 表示该文本属于内部枚举，不应直接展示给玩家。
 * 异常：不抛异常；空字符串按非内部标记处理，由调用方决定兜底。
 */
function isInternalSceneObjectMarker(value: string, objectType: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return normalized === objectType.trim().toLowerCase() || normalized in SCENE_OBJECT_TYPE_LABELS;
}

/**
 * 功能：判断文本是否明显包含系统标识符，用于决定 UI 是否需要转成人类可读标签。
 * 入参：value（string）：后端返回的标签、ID 或动作文本。
 * 出参：boolean，true 表示文本中存在 snake_case、命名空间前缀等系统标识符痕迹。
 * 异常：不抛异常；空字符串按非系统标识符处理。
 */
export function containsSystemIdentifier(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  return /(?:\b[a-z][a-z0-9]*:)?[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b/i.test(trimmed);
}

/**
 * 功能：判断文本是否是纯系统 ID，而不是包含中文语义的动作句。
 * 入参：value（string）：后端返回的原始文本。
 * 出参：boolean，true 表示可按 ID 兜底格式化。
 * 异常：不抛异常；空字符串返回 false。
 */
export function isSystemIdentifier(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  return /^[a-z][a-z0-9]*(?::[a-z][a-z0-9_]*)?$/i.test(trimmed)
    ? containsSystemIdentifier(trimmed)
    : /^[a-z][a-z0-9]*(?:[_:-][a-z0-9]+)+$/i.test(trimmed);
}

/**
 * 功能：把系统 ID 降级格式化为玩家可读文本，避免主界面直接暴露下划线代码。
 * 入参：value（string）：可能带命名空间或 snake_case 的系统 ID。
 * 出参：string，去除命名空间并替换分隔符后的显示文本。
 * 异常：不抛异常；空值返回空字符串。
 */
export function formatSystemIdentifierForDisplay(value: string): string {
  const label = SYSTEM_IDENTIFIER_LABELS[normalizeSystemIdentifierKey(value)];
  if (label) {
    return label;
  }
  return value
    .trim()
    .replace(/^[a-z][a-z0-9]*:/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
}

/**
 * 功能：清理场景标签中的动作前后缀，提取更适合作为目标名或物品名的显示文本。
 * 入参：value（string）：剧本对象标签、affordance 标签或原始 ID。
 * 出参：string，优先保留剧本提供的可读文本；系统 ID 仅作为弱兜底格式化。
 * 异常：不抛异常；清理后为空时返回原始值的格式化结果。
 */
export function cleanSceneDisplayLabel(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  const withoutPrefix = trimmed
    .replace(/^(?:询问|检查|查看|观察|搜索|调查|研究|阅读|点燃|使用|打开|攻击|呼唤|前往|进入|走向|和|与|同|向|对)\s*/u, "")
    .trim();
  const withoutSuffix = withoutPrefix.replace(/\s*(?:交谈|对话|谈话)$/u, "").trim();
  const cleaned = withoutSuffix || withoutPrefix || trimmed;
  return containsSystemIdentifier(cleaned)
    ? formatSystemIdentifierForDisplay(cleaned)
    : cleaned;
}

/**
 * 功能：为正则替换转义系统 ID，避免 ID 内特殊字符影响按钮标签重写。
 * 入参：value（string）：需要作为字面量匹配的系统 ID。
 * 出参：string，可安全放入 RegExp 的转义文本。
 * 异常：不抛异常；所有字符按字面量处理。
 */
export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 功能：在显示名解析表中按可信度写入候选标签，避免弱 ID 兜底覆盖剧本显式名称。
 * 入参：resolver（SceneDisplayLabelResolver）：待更新映射；scores（Map<string, number>）：候选可信度；
 *   rawKey（unknown）：系统 ID；rawLabel（unknown）：候选显示文本；baseScore（number）：来源基础分。
 * 出参：void，原地更新 resolver 与 scores。
 * 异常：不抛异常；缺失 key/label 时直接跳过。
 */
export function setSceneDisplayLabelCandidate(
  resolver: SceneDisplayLabelResolver,
  scores: Map<string, number>,
  rawKey: unknown,
  rawLabel: unknown,
  baseScore: number
): void {
  const key = textValue(rawKey, "").trim();
  const rawText = textValue(rawLabel, "").trim();
  if (!key || !rawText) {
    return;
  }
  const label = cleanSceneDisplayLabel(rawText);
  if (!label) {
    return;
  }
  const score =
    baseScore +
    (isSystemIdentifier(rawText) ? 0 : 50) +
    (label === key ? -30 : 0);
  if ((scores.get(key) ?? Number.NEGATIVE_INFINITY) < score) {
    resolver.set(key, label);
    scores.set(key, score);
  }
}

/**
 * 功能：从当前场景构造系统 ID 到玩家可读名称的映射，供摘要、对象卡片和动作按钮复用。
 * 入参：scene（SceneSnapshot | null）：后端返回的场景快照。
 * 出参：SceneDisplayLabelResolver，键为对象/实体/物品/位置 ID，值为 UI 显示名。
 * 异常：不抛异常；字段缺失时返回空映射并由调用方兜底。
 */
export function buildSceneDisplayLabelResolver(
  scene: SceneSnapshot | null
): SceneDisplayLabelResolver {
  const resolver: SceneDisplayLabelResolver = new Map();
  const scores = new Map<string, number>();
  const sourceRefKeys = [
    "target_ref",
    "entity_id",
    "item_id",
    "location_id",
    "target_scene_id",
    "interaction_id",
    "object_id",
    "id",
  ];

  const currentLocation = scene?.current_location ?? {};
  const currentLocationLabel =
    currentLocation.display_name ?? currentLocation.name ?? currentLocation.label;
  setSceneDisplayLabelCandidate(
    resolver,
    scores,
    currentLocation.location_id ?? currentLocation.scene_id ?? currentLocation.id,
    currentLocationLabel,
    80
  );

  for (const item of scene?.scene_objects ?? []) {
    setSceneDisplayLabelCandidate(resolver, scores, item.object_id, item.label, 60);
    const sourceRef = item.source_ref ?? {};
    for (const key of sourceRefKeys) {
      setSceneDisplayLabelCandidate(resolver, scores, sourceRef[key], item.label, 70);
    }
  }

  for (const exit of scene?.exits ?? []) {
    const label = exit.name ?? exit.label ?? exit.display_name;
    setSceneDisplayLabelCandidate(resolver, scores, exit.to_location_id, label, 75);
    setSceneDisplayLabelCandidate(resolver, scores, exit.target_scene_id, label, 75);
    setSceneDisplayLabelCandidate(resolver, scores, exit.id, label, 70);
  }

  for (const affordance of scene?.affordances ?? []) {
    const label = affordance.label || affordance.user_input || "";
    setSceneDisplayLabelCandidate(resolver, scores, affordance.target_id, label, 45);
    setSceneDisplayLabelCandidate(resolver, scores, affordance.location_id, label, 45);
    setSceneDisplayLabelCandidate(resolver, scores, affordance.object_id, label, 40);
  }

  for (const npc of scene?.visible_npcs ?? []) {
    const label = npc.name ?? npc.label;
    setSceneDisplayLabelCandidate(resolver, scores, npc.entity_id, label, 65);
    setSceneDisplayLabelCandidate(resolver, scores, npc.id, label, 65);
  }

  for (const item of scene?.visible_items ?? []) {
    const label = item.name ?? item.label;
    setSceneDisplayLabelCandidate(resolver, scores, item.item_id, label, 65);
    setSceneDisplayLabelCandidate(resolver, scores, item.id, label, 65);
  }

  return resolver;
}

/**
 * 功能：按候选字段解析场景摘要里的目标、物品或出口显示名。
 * 入参：source（Record<string, unknown>）：后端对象；candidateKeys（string[]）：按优先级读取的字段；
 *   resolver（SceneDisplayLabelResolver）：系统 ID 到显示名映射；fallback（string）：空值兜底文案。
 * 出参：string，适合直接展示给玩家的文本。
 * 异常：不抛异常；所有字段缺失时返回 fallback。
 */
export function resolveSceneDisplayLabelFromRecord(
  source: Record<string, unknown>,
  candidateKeys: string[],
  resolver: SceneDisplayLabelResolver,
  fallback: string
): string {
  const candidates = candidateKeys
    .map((key) => textValue(source[key], "").trim())
    .filter(Boolean);
  for (const candidate of candidates) {
    const resolved = resolver.get(candidate);
    if (resolved) {
      return resolved;
    }
  }
  for (const candidate of candidates) {
    if (!containsSystemIdentifier(candidate)) {
      return cleanSceneDisplayLabel(candidate);
    }
  }
  return candidates.length ? cleanSceneDisplayLabel(candidates[0]) : fallback;
}

/**
 * 功能：解析场景对象卡片标题，优先展示剧本标签，必要时用 source_ref 找目标显示名。
 * 入参：item（SceneObjectRef）：后端场景对象；resolver（SceneDisplayLabelResolver）：显示名映射。
 * 出参：string，对象卡片标题。
 * 异常：不抛异常；无法解析时返回“可交互目标”。
 */
export function resolveSceneObjectDisplayLabel(
  item: SceneObjectRef,
  resolver: SceneDisplayLabelResolver
): string {
  const rawLabel = textValue(item.label, "").trim();
  if (rawLabel && !containsSystemIdentifier(rawLabel)) {
    return rawLabel;
  }
  if (rawLabel) {
    const rewritten = rewriteActionLabelForDisplay(rawLabel, resolver);
    if (rewritten && !containsSystemIdentifier(rewritten)) {
      return rewritten;
    }
  }
  const sourceRef = item.source_ref ?? {};
  return resolveSceneDisplayLabelFromRecord(
    {
      object_id: item.object_id,
      label: item.label,
      target_ref: sourceRef.target_ref,
      entity_id: sourceRef.entity_id,
      item_id: sourceRef.item_id,
      location_id: sourceRef.location_id,
    },
    ["target_ref", "entity_id", "item_id", "location_id", "object_id", "label"],
    resolver,
    "可交互目标"
  );
}

/**
 * 功能：把后端 scene object 类型枚举转成玩家可读标签。
 * 入参：objectType（string）：后端 object_type，例如 location、exit、npc。
 * 出参：string，已知类型返回中文标签，未知类型返回清理后的可读文本或“对象”。
 * 异常：不抛异常；空值返回“对象”。
 */
export function resolveSceneObjectTypeLabel(objectType: string): string {
  const normalized = objectType.trim().toLowerCase();
  if (!normalized) {
    return "对象";
  }
  return SCENE_OBJECT_TYPE_LABELS[normalized] ?? cleanSceneDisplayLabel(objectType) ?? "对象";
}

/**
 * 功能：解析场景对象描述，过滤 location/exit 等内部枚举，避免代码字段泄漏到玩家视图。
 * 入参：item（SceneObjectRef）：后端场景对象；resolver（SceneDisplayLabelResolver）：显示名映射。
 * 出参：string，玩家可读描述；原始描述缺失或只是内部标记时返回类型化兜底文案。
 * 异常：不抛异常；系统 ID 描述按兜底处理，避免主界面出现代码样式文本。
 */
export function resolveSceneObjectDescription(
  item: SceneObjectRef,
  resolver: SceneDisplayLabelResolver
): string {
  const objectType = textValue(item.object_type, "").trim().toLowerCase();
  const fallback = SCENE_OBJECT_DESCRIPTION_FALLBACKS[objectType] ?? "可交互目标。";
  const rawDescription = textValue(item.description, "").trim();
  if (!rawDescription || isInternalSceneObjectMarker(rawDescription, objectType)) {
    return fallback;
  }
  const rewritten = rewriteActionLabelForDisplay(rawDescription, resolver).trim();
  if (
    !rewritten ||
    isInternalSceneObjectMarker(rewritten, objectType) ||
    isSystemIdentifier(rewritten)
  ) {
    return fallback;
  }
  return rewritten;
}

/**
 * 功能：把动作按钮文本里的系统 ID 替换为玩家可读名，但保留原动作值供提交。
 * 入参：action（string）：后端返回的可提交动作文本；resolver（SceneDisplayLabelResolver）：显示名映射。
 * 出参：string，仅用于按钮渲染的标签。
 * 异常：不抛异常；没有映射时将残留 snake_case 降级为空格分隔文本。
 */
export function rewriteActionLabelForDisplay(
  action: string,
  resolver: SceneDisplayLabelResolver
): string {
  let label = action;
  const entries = [...resolver.entries()]
    .filter(([key, value]) => key && value && key !== value)
    .sort(([left], [right]) => right.length - left.length);
  for (const [key, value] of entries) {
    label = label.replace(new RegExp(escapeRegExp(key), "g"), value);
  }
  const withoutSystemIds = label.replace(
    /(?:\b[a-z][a-z0-9]*:)?[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b/gi,
    (match) => formatSystemIdentifierForDisplay(match)
  );
  return withoutSystemIds.replace(
    /^(前往|进入|检查|查看|询问|攻击|使用|打开|呼唤|观察)\1/u,
    "$1"
  );
}
