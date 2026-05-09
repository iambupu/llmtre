from __future__ import annotations

import json
from pathlib import Path

from game_workflows import main_loop_config


def test_load_main_loop_rules_returns_defaults_when_file_missing_or_invalid(
    tmp_path: Path,
) -> None:
    """
    功能：验证配置文件缺失、坏 JSON、非 dict JSON 都会降级为默认规则。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示配置缺失/非法类型回退回归。
    """
    default_attack_dc = main_loop_config.DEFAULT_MAIN_LOOP_RULES["resolution"]["attack"]["base_dc"]

    main_loop_config.load_main_loop_rules.cache_clear()
    missing = main_loop_config.load_main_loop_rules(str(tmp_path / "missing.json"))
    assert missing["resolution"]["attack"]["base_dc"] == default_attack_dc

    bad_json_path = tmp_path / "bad.json"
    bad_json_path.write_text("{bad", encoding="utf-8")
    main_loop_config.load_main_loop_rules.cache_clear()
    bad_json = main_loop_config.load_main_loop_rules(str(bad_json_path))
    assert bad_json["resolution"]["attack"]["base_dc"] == default_attack_dc

    list_json_path = tmp_path / "list.json"
    list_json_path.write_text("[1, 2, 3]", encoding="utf-8")
    main_loop_config.load_main_loop_rules.cache_clear()
    list_json = main_loop_config.load_main_loop_rules(str(list_json_path))
    assert list_json["resolution"]["attack"]["base_dc"] == default_attack_dc


def test_load_main_loop_rules_deep_merges_nested_overrides(tmp_path: Path) -> None:
    """
    功能：验证配置覆盖会深合并嵌套字典，并保留未覆盖的默认规则。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示配置深合并语义回归。
    """
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "resolution": {"attack": {"base_dc": 14}},
                "outer_loop": {"max_pending_tasks": 3},
                "nlu": {"target_aliases": {"dragon_01": ["龙"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    main_loop_config.load_main_loop_rules.cache_clear()
    rules = main_loop_config.load_main_loop_rules(str(rules_path))

    assert rules["resolution"]["attack"]["base_dc"] == 14
    assert rules["resolution"]["attack"]["damage_dice"] == "d6"
    assert rules["outer_loop"]["max_pending_tasks"] == 3
    assert rules["outer_loop"]["emit_world_evolution"] is True
    assert rules["nlu"]["target_aliases"]["dragon_01"] == ["龙"]
    assert rules["nlu"]["target_aliases"]["player_01"] == ["旅行者", "player"]
    assert rules["default_story_policy"]["background"] == "unfixed"


def test_deep_merge_replaces_non_dict_values() -> None:
    """
    功能：验证 `_deep_merge` 在 override 非 dict 时直接替换，避免错误递归。
    入参：无。
    出参：None。
    异常：断言失败表示配置类型覆盖语义回归。
    """
    merged = main_loop_config._deep_merge(  # noqa: SLF001
        {"outer_loop": {"max_pending_tasks": 64}, "rag": {"read_only_enabled": True}},
        {"outer_loop": "disabled", "rag": {"read_only_enabled": False}},
    )

    assert merged["outer_loop"] == "disabled"
    assert merged["rag"]["read_only_enabled"] is False
