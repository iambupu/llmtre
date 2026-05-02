from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from typing import Any

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RULES_PATH = os.path.join(BASE_DIR, "config", "main_loop_rules.json")

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
            "goblin_01": ["地精", "goblin"],
            "player_01": ["旅行者", "player"],
        },
        "location_aliases": {
            "forest_edge": ["森林", "forest"],
            "camp": ["营地", "camp"],
        },
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
def load_main_loop_rules(path: str = RULES_PATH) -> dict[str, Any]:
    """
    功能：加载配置或数据资源。
    入参：path。
    出参：dict[str, Any]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    rules = copy.deepcopy(DEFAULT_MAIN_LOOP_RULES)
    if not os.path.exists(path):
        return rules

    try:
        with open(path, encoding="utf-8") as file:
            loaded = json.load(file)
    except (json.JSONDecodeError, OSError):
        return rules

    if not isinstance(loaded, dict):
        return rules
    return _deep_merge(rules, loaded)
