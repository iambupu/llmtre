import type { ActiveCharacter, CharacterStatusEffect } from "@/types";
import { textValue } from "@/play/displayTextSupport";
import { formatSystemIdentifierForDisplay } from "@/play/sceneDisplaySupport";

export type MetricValue = {
  current: number;
  max: number;
};

export type CharacterMetrics = {
  hp: MetricValue | null;
  mp: MetricValue | null;
};

/**
 * 功能：从可能不同命名的角色字段中解析 HP/MP 数值。
 * 入参：character（Record<string, unknown> | null | undefined）：后端 active_character。
 * 出参：{ hp, mp }，字段缺失时返回 null，表示 UI 使用占位态。
 * 异常：不抛异常；非数值字段会被忽略并降级为空状态。
 */
export function parseCharacterMetrics(
  character: Record<string, unknown> | null | undefined
): CharacterMetrics {
  if (!character) {
    return { hp: null, mp: null };
  }
  const hpCurrent = Number(character.hp ?? character.current_hp);
  const hpMax = Number(character.max_hp ?? character.hp_max);
  const mpCurrent = Number(character.mp ?? character.current_mp);
  const mpMax = Number(character.max_mp ?? character.mp_max);
  return {
    hp:
      Number.isFinite(hpCurrent) && Number.isFinite(hpMax)
        ? { current: hpCurrent, max: hpMax }
        : null,
    mp:
      Number.isFinite(mpCurrent) && Number.isFinite(mpMax)
        ? { current: mpCurrent, max: mpMax }
        : null,
  };
}

/**
 * 功能：优先使用 inventory_items 作为背包展示源，缺失时再降级到 inventory。
 * 入参：character（Record<string, unknown> | null）：当前角色快照。
 * 出参：unknown[]，用于背包与装备卡片渲染的条目列表。
 * 异常：不抛异常；字段类型不匹配时返回空数组。
 */
export function resolveInventoryItems(
  character: ActiveCharacter | null
): unknown[] {
  if (!character) {
    return [];
  }
  const readable = character.inventory_items;
  if (Array.isArray(readable) && readable.length) {
    return readable;
  }
  return Array.isArray(character.inventory) ? (character.inventory as unknown[]) : [];
}

/**
 * 功能：从后端角色快照中读取派生状态效果，前端只展示，不自行推断规则状态。
 * 入参：character（ActiveCharacter | null）：后端 active_character 快照。
 * 出参：CharacterStatusEffect[]，字段非法时返回空数组。
 * 异常：不抛异常；非对象条目会被过滤，避免调试态脏数据影响页面。
 */
export function resolveStatusEffects(character: ActiveCharacter | null): CharacterStatusEffect[] {
  const rawEffects = character?.status_effects;
  if (!Array.isArray(rawEffects)) {
    return [];
  }
  return rawEffects
    .filter((item): item is CharacterStatusEffect => Boolean(item) && typeof item === "object")
    .map((item) => ({
      key: textValue(item.key, "unknown"),
      label: formatSystemIdentifierForDisplay(
        textValue(item.label ?? item.key, "未知状态")
      ),
      kind: textValue(item.kind, "flag"),
      severity: textValue(item.severity, "info"),
      description: textValue(item.description, ""),
    }));
}

/**
 * 功能：生成玩家可读的角色状态摘要。
 * 入参：character（ActiveCharacter | null）：后端 active_character 快照。
 * 出参：string，优先由状态效果标签拼接，缺失时把后端 status_summary 中的内部 key 转成中文。
 * 异常：不抛异常；缺失或空白状态返回“状态稳定”。
 */
export function resolveStatusSummary(character: ActiveCharacter | null): string {
  const effects = resolveStatusEffects(character);
  if (effects.length) {
    return effects.map((effect) => effect.label).join("、");
  }
  const rawSummary = textValue(character?.status_summary, "");
  if (!rawSummary) {
    return "状态稳定";
  }
  return rawSummary
    .split(/[、,，]/u)
    .map((part) => formatSystemIdentifierForDisplay(part))
    .filter(Boolean)
    .join("、");
}
