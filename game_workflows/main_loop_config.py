from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

import yaml

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RULES_PATH = os.path.join(BASE_DIR, "config", "main_loop_rules.json")
MOD_REGISTRY_PATH = os.path.join(BASE_DIR, "config", "mod_registry.yml")
MODS_ROOT = os.path.join(BASE_DIR, "mods")
SCENARIO_RULES_ENV = "LLMTRE_SCENARIO_RULES_PATH"
EXTRA_RULES_ENV = "LLMTRE_MAIN_LOOP_RULES_EXTRA"

DEFAULT_MAIN_LOOP_RULES: dict[str, Any] = {
    "nlu": {
        "action_keywords": {
            "attack": ["攻击", "attack", "砍", "挥剑", "射击", "反击"],
            "talk": ["交谈", "对话", "talk", "说话", "询问", "打听", "问", "劝说", "威胁"],
            "move": [
                "移动",
                "前往",
                "move",
                "去",
                "继续",
                "前进",
                "赶路",
                "走",
                "靠近",
                "离开",
                "进入",
                "返回",
                "潜行",
            ],
            "observe": ["观察", "看看", "环顾", "周围", "look", "observe", "听", "闻"],
            "wait": ["等待", "坐一会", "停一会", "待一会", "wait"],
            "rest": ["休息", "小憩", "恢复", "rest"],
            "inspect": ["检查", "查看", "inspect", "搜索", "搜查", "翻找", "寻找"],
            "use_item": ["喝下", "服用", "使用药水", "potion", "use", "使用"],
            "interact": [
                "调查",
                "互动",
                "interact",
                "拾取",
                "捡起",
                "拿起",
                "打开",
                "关闭",
                "推开",
                "拉动",
                "触摸",
                "给予",
                "交给",
                "装备",
            ],
            "commit_sandbox": ["并入主线", "合并沙盒", "确认并入"],
            "discard_sandbox": ["回滚沙盒", "放弃沙盒", "取消沙盒"],
        },
        "target_aliases": {
            "player_01": ["旅行者", "player"],
        },
        "location_aliases": {},
        "item_aliases": {
            "health_potion_01": ["药水", "potion"],
        },
    },
    "resolution": {
        "attack": {
            "base_dc": 10,
            "agility_divisor": 2,
            "damage_dice": "d6",
            "strength_damage_divisor": 3,
            "min_damage": 1,
        },
        "move": {"mp_delta": -1, "state_flags_add": ["moved_recently"]},
        "talk": {"mp_delta": -1, "state_flags_add": ["conversation_started"]},
        "interact": {"mp_delta": -1, "state_flags_add": ["observed_surroundings"]},
        "commit_sandbox": {"state_flags_add": ["sandbox_merged"]},
        "discard_sandbox": {"state_flags_add": ["sandbox_discarded"]},
    },
    "rag": {
        "read_only_enabled": True,
        "auto_initialize": True,
        "query_template": "玩家[{active_character_id}]输入：{user_input}",
    },
    "memory": {
        "summary_step": 0,
        "summary_context_size": 20,
    },
    "default_story_policy": {
        "mode": "open_seed",
        "background": "unfixed",
        "instruction": (
            "默认剧本不是固定世界或固定关卡。每个新会话从空白、低约束的冒险起点生成，"
            "地点、目标、冲突、NPC 和线索都应由本次开场叙事临场创建；"
            "不要默认使用森林、营地、地精或任何固定背景。"
        ),
    },
    "character_status": {
        "stable_summary": "状态稳定",
        "resource_thresholds": {
            "hp_critical_ratio": 0.25,
            "hp_wounded_ratio": 0.5,
            "mp_low_ratio": 0.25,
        },
        "resource_effects": {
            "hp_critical": {
                "key": "hp_critical",
                "label": "濒危",
                "kind": "resource",
                "severity": "critical",
                "description": "生命值极低，行动叙事应体现明显危险。",
            },
            "hp_wounded": {
                "key": "hp_wounded",
                "label": "受伤",
                "kind": "resource",
                "severity": "warning",
                "description": "生命值低于安全线，行动叙事应体现体力受损。",
            },
            "mp_low": {
                "key": "mp_low",
                "label": "法力不足",
                "kind": "resource",
                "severity": "warning",
                "description": "法力值较低，施法或持续行动应显得吃力。",
            },
        },
        "flags": {
            "moved_recently": {
                "label": "刚刚移动",
                "kind": "activity",
                "severity": "info",
                "description": "角色刚完成位置变更，叙事可承接位移后的观察。",
            },
            "conversation_started": {
                "label": "交谈中",
                "kind": "social",
                "severity": "info",
                "description": "角色已进入对话节奏，叙事可承接 NPC 反馈。",
            },
            "observed_surroundings": {
                "label": "警觉观察",
                "kind": "awareness",
                "severity": "info",
                "description": "角色正在留意周围细节，叙事可强调环境线索。",
            },
            "waited_recently": {
                "label": "短暂停留",
                "kind": "activity",
                "severity": "info",
                "description": "角色刚刚等待片刻，叙事可体现时间流逝。",
            },
            "sandbox_merged": {
                "label": "沙盒已并入",
                "kind": "sandbox",
                "severity": "info",
                "description": "沙盒剧情已进入主线。",
            },
            "sandbox_discarded": {
                "label": "沙盒已回滚",
                "kind": "sandbox",
                "severity": "info",
                "description": "沙盒剧情已被放弃并回到主线状态。",
            },
        },
    },
    "outer_loop": {
        "default_bridge": "workflow",
        "emit_world_evolution": True,
        "world_evolution_minutes_per_turn": 10,
        "emit_timeout_seconds": 8.0,
        "max_pending_tasks": 64,
        "outbox_replay_limit": 10,
        "outbox_replay_interval_seconds": 2.0,
        "outbox_max_attempts": 5,
        "outbox_backoff_seconds": 5,
        "outbox_processing_timeout_seconds": 30,
        "achievement_rewards": {
            "first_blood": {"mp_delta": 1},
            "keen_observer": {"hp_delta": 1},
        },
    },
    "narrative_templates": {
        "invalid": "{actor_name}的行动未能成立：{errors}",
        "idle": "{actor_name}暂时没有采取有效行动。",
        "attack_hit": (
            "{actor_name}发起了攻击，判定 {attack_roll} 超过 {attack_dc}，"
            "对 {target_id} 造成了 {damage} 点伤害。"
        ),
        "attack_miss": (
            "{actor_name}发起了攻击，但未能命中 {target_id}。"
            "判定 {attack_roll} 未达到 {attack_dc}。"
        ),
        "talk": "{actor_name}与 {target_id} 进行交谈，消耗了 {mp_cost} 点法力。",
        "move": "{actor_name}前往了 {location_id}，消耗了 {mp_cost} 点法力。",
        "use_item": "{actor_name}使用了 {item_id}，恢复了 {hp_delta} 点生命。",
        "interact": "{actor_name}仔细观察了周围环境，消耗了 {mp_cost} 点法力。",
        "commit_sandbox": "{actor_name}将沙盒剧情并入了主线，当前世界状态已更新。",
        "discard_sandbox": "{actor_name}放弃了沙盒剧情，世界状态已回滚到主线。",
        "default": "{actor_name}完成了 {action_type} 行动。",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    功能：执行 `_deep_merge` 相关业务逻辑。
    入参：base；override。
    出参：dict[str, Any]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def _load_main_loop_rules_cached(
    path: str,
    override_paths: tuple[str, ...],
    signature: str,
) -> dict[str, Any]:
    """
    功能：在稳定签名下缓存并组装主循环规则，避免每回合重复读取磁盘。
    入参：path（str）：基础规则文件路径；
        override_paths（tuple[str, ...]）：按优先级排序的覆盖文件路径；
        signature（str）：文件签名，驱动缓存失效。
    出参：dict[str, Any]，合并后的主循环规则。
    异常：文件读取异常内部降级，不向上抛出；签名仅用于缓存键，不参与逻辑判断。
    """
    del signature
    rules = copy.deepcopy(DEFAULT_MAIN_LOOP_RULES)
    loaded = _load_json_mapping(path)
    if loaded:
        rules = _deep_merge(rules, loaded)
    for override_path in override_paths:
        override = _load_json_mapping(override_path)
        if not override:
            continue
        # 事务边界：覆盖仅作用于规则快照，不直接写入世界状态。
        rules = _deep_merge(rules, override)
    return rules


def _load_json_mapping(path: str) -> dict[str, Any]:
    """
    功能：读取 JSON 对象文件并收敛为映射，供规则分层合并复用。
    入参：path（str）：JSON 文件路径。
    出参：dict[str, Any]，成功返回对象；失败返回空字典。
    异常：JSON 语法或 IO 异常内部捕获并降级为空字典。
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as file:
            loaded = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_mod_registry() -> dict[str, Any]:
    """
    功能：读取 mod_registry.yml，解析当前启用模组清单。
    入参：无，路径固定为 `config/mod_registry.yml`。
    出参：dict[str, Any]，成功返回注册表对象；失败返回空字典。
    异常：YAML 解析或 IO 异常内部捕获并降级为空字典。
    """
    if not os.path.exists(MOD_REGISTRY_PATH):
        return {}
    try:
        with open(MOD_REGISTRY_PATH, encoding="utf-8") as file:
            loaded = yaml.safe_load(file)
    except (yaml.YAMLError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _extract_enabled_mod_ids(registry: dict[str, Any]) -> list[str]:
    """
    功能：从注册表提取按优先级排序的启用模组 ID 列表。
    入参：registry（dict[str, Any]）：mod_registry.yml 对象。
    出参：list[str]，仅包含 `enabled=true` 且有 `mod_id` 的模组。
    异常：不抛异常；结构异常时返回空列表。
    """
    active_mods = registry.get("active_mods", [])
    if not isinstance(active_mods, list):
        return []
    ranked: list[tuple[int, str]] = []
    for item in active_mods:
        if not isinstance(item, dict) or not bool(item.get("enabled", False)):
            continue
        mod_id = str(item.get("mod_id") or "").strip()
        if not mod_id:
            continue
        priority = int(item.get("priority", 0)) if isinstance(item.get("priority", 0), int) else 0
        ranked.append((priority, mod_id))
    ranked.sort(key=lambda pair: pair[0])
    return [mod_id for _, mod_id in ranked]


def _collect_rule_override_paths(path: str) -> list[str]:
    """
    功能：收集规则覆盖层路径，顺序为基础文件之后的应用顺序（低优先级在前）。
    入参：path（str）：基础规则路径，用于避免重复加入同一路径。
    出参：list[str]，包含启用模组覆盖、剧本覆盖与额外覆盖路径。
    异常：不抛异常；缺失路径会在后续读取阶段自动跳过。
    """
    result: list[str] = []
    seen: set[str] = {os.path.abspath(path)}

    registry = _load_mod_registry()
    mod_ids = _extract_enabled_mod_ids(registry)
    for mod_id in mod_ids:
        mod_dir = os.path.join(MODS_ROOT, mod_id)
        candidates = [
            os.path.join(mod_dir, "main_loop_rules.override.json"),
            os.path.join(mod_dir, "rules", "main_loop_rules.override.json"),
            os.path.join(mod_dir, "rules", "main_loop_rules.json"),
        ]
        for candidate in candidates:
            abs_candidate = os.path.abspath(candidate)
            if abs_candidate in seen:
                continue
            seen.add(abs_candidate)
            result.append(abs_candidate)

    scenario_path = str(os.getenv(SCENARIO_RULES_ENV, "")).strip()
    if scenario_path:
        abs_scenario = os.path.abspath(scenario_path)
        if abs_scenario not in seen:
            seen.add(abs_scenario)
            result.append(abs_scenario)

    extra_paths_raw = str(os.getenv(EXTRA_RULES_ENV, "")).strip()
    if extra_paths_raw:
        for extra in [entry.strip() for entry in extra_paths_raw.split(";") if entry.strip()]:
            abs_extra = os.path.abspath(extra)
            if abs_extra in seen:
                continue
            seen.add(abs_extra)
            result.append(abs_extra)
    return result


def _build_rules_signature(paths: Iterable[str]) -> str:
    """
    功能：基于路径元数据构建规则签名，用于缓存失效与热更新感知。
    入参：paths（Iterable[str]）：所有参与加载的规则路径。
    出参：str，稳定签名字符串。
    异常：不抛异常；缺失路径以 `missing` 记录，IO 异常降级为 `error` 标记。
    """
    chunks: list[str] = []
    for path in paths:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            chunks.append(f"{abs_path}:missing")
            continue
        try:
            stat = os.stat(abs_path)
            chunks.append(f"{abs_path}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            chunks.append(f"{abs_path}:error")
    return "|".join(chunks)


def load_main_loop_rules(path: str = RULES_PATH) -> dict[str, Any]:
    """
    功能：加载主循环规则，支持“基础规则 + 启用模组覆盖 + 剧本覆盖 + 额外覆盖”分层。
    入参：path（str）：基础规则路径，默认 `config/main_loop_rules.json`。
    出参：dict[str, Any]，深度合并后的规则快照。
    异常：读取失败时内部降级为默认规则，不抛异常阻断主流程。
    """
    override_paths = _collect_rule_override_paths(path)
    signature = _build_rules_signature([path, *override_paths])
    merged = _load_main_loop_rules_cached(path, tuple(override_paths), signature)
    return copy.deepcopy(merged)


def clear_main_loop_rules_cache() -> None:
    """
    功能：清理主循环规则缓存，供测试和配置热更新前置步骤使用。
    入参：无。
    出参：None。
    异常：不抛异常；委托 lru_cache 的 cache_clear 完成内部状态清理。
    """
    _load_main_loop_rules_cached.cache_clear()


# 兼容契约：历史测试和调用方通过 load_main_loop_rules.cache_clear() 清理规则缓存。
setattr(load_main_loop_rules, "cache_clear", clear_main_loop_rules_cache)
