from __future__ import annotations

from game_workflows.affordances import build_scene_interaction_model


def test_scene_affordances_include_baseline_actions() -> None:
    """
    功能：验证场景交互模型始终包含 A1 保底动作，确保无对象场景也可继续推进回合。
    入参：无。
    出参：None。
    异常：断言失败表示 affordances 保底分支回归。
    """
    model = build_scene_interaction_model(
        scene_snapshot={
            "current_location": {"id": "road", "name": "道路"},
            "exits": [],
            "visible_npcs": [],
            "visible_items": [],
        },
        active_character=None,
    )

    affordance_inputs = {item["user_input"] for item in model["affordances"]}
    assert "观察周围" in affordance_inputs
    assert "等待片刻" in affordance_inputs
    assert "短暂休息" in affordance_inputs


def test_scene_affordances_deduplicate_same_default_input() -> None:
    """
    功能：验证同一 default_input 的交互槽会在 affordances 层去重，避免前端出现重复候选动作。
    入参：无。
    出参：None。
    异常：断言失败表示 `_build_affordances` 去重规则失效。
    """
    model = build_scene_interaction_model(
        scene_snapshot={
            "current_location": {"id": "road", "name": "道路"},
            "exits": [
                {"location_id": "north_1", "label": "北门"},
                {"location_id": "north_2", "label": "北门"},
            ],
            "visible_npcs": [],
            "visible_items": [],
        },
        active_character=None,
    )

    move_to_north = [
        item for item in model["affordances"] if item["user_input"] == "前往北门"
    ]
    assert len(move_to_north) == 1


def test_scene_affordances_project_scene_objects_to_enabled_actions() -> None:
    """
    功能：验证出口、NPC、可见物品与背包物品会投影为可执行 affordance，并保留来源 ID。
    入参：无。
    出参：None。
    异常：断言失败表示 affordance 构建规则或来源追溯字段回归。
    """
    model = build_scene_interaction_model(
        scene_snapshot={
            "current_location": {"id": "road", "name": "道路"},
            "exits": [{"location_id": "forest", "label": "森林"}],
            "visible_npcs": [{"entity_id": "npc_guard", "name": "守卫"}],
            "visible_items": [{"item_id": "old_key", "name": "旧钥匙"}],
        },
        active_character={
            "inventory_items": [{"item_id": "healing_potion", "name": "治疗药水"}],
        },
    )

    affordances = {item["user_input"]: item for item in model["affordances"]}
    assert affordances["前往森林"]["action_type"] == "move"
    assert affordances["前往森林"]["location_id"] == "forest"
    assert affordances["和守卫交谈"]["action_type"] == "talk"
    assert affordances["和守卫交谈"]["target_id"] == "npc_guard"
    assert affordances["攻击守卫"]["action_type"] == "attack"
    assert affordances["检查旧钥匙"]["action_type"] == "interact"
    assert affordances["检查旧钥匙"]["target_id"] == "old_key"
    assert affordances["使用治疗药水"]["action_type"] == "use_item"
    assert affordances["使用治疗药水"]["target_id"] == "healing_potion"
    assert all(item["enabled"] is True for item in affordances.values())


def test_scene_affordances_tolerate_dirty_scene_and_character_fields() -> None:
    """
    功能：验证场景快照和角色背包字段类型异常时会降级为空集合并保留默认候选动作。
    入参：无。
    出参：None。
    异常：断言失败表示脏字段降级或默认候选动作回归。
    """
    model = build_scene_interaction_model(
        scene_snapshot={
            "current_location": "bad-location",
            "exits": "bad-exits",
            "visible_npcs": [{"name": "缺 ID 的 NPC"}, "bad-npc"],
            "visible_items": [{"name": "缺 ID 的物品"}, "bad-item"],
        },
        active_character={"inventory_items": [{"name": "缺 item_id 的背包物"}, "bad-inv"]},
    )

    object_ids = {item["object_id"] for item in model["scene_objects"]}
    affordance_inputs = {item["user_input"] for item in model["affordances"]}

    assert object_ids == {"location:unknown"}
    assert "检查周围" in affordance_inputs
    assert "观察周围" in affordance_inputs
    assert "等待片刻" in affordance_inputs
    assert "短暂休息" in affordance_inputs
    assert model["ui_hints"]["primary_object_ids"] == ["location:unknown"]
