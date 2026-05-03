from __future__ import annotations

from game_workflows.affordances import build_scene_interaction_model
from state.contracts.scene import SceneSnapshotV2


def test_scene_snapshot_v2_projects_objects_slots_and_affordances() -> None:
    """
    功能：验证 A1 场景快照能投影 A2 预留对象、交互槽和行动候选。
    入参：无，使用内联场景夹具。
    出参：None，通过断言表达预期。
    异常：断言失败表示 SceneSnapshot v2 预留接口不可消费。
    """
    scene = {
        "current_location": {"id": "camp", "name": "营地", "description": "篝火旁。"},
        "exits": [{"location_id": "forest_edge", "label": "森林边缘", "direction": "forward"}],
        "visible_npcs": [{"entity_id": "guard_01", "name": "守卫"}],
        "visible_items": [{"item_id": "key_01", "name": "铜钥匙"}],
        "active_quests": [],
        "recent_memory": "",
        "available_actions": [],
        "suggested_actions": [],
    }

    scene.update(build_scene_interaction_model(scene))
    snapshot = SceneSnapshotV2.model_validate(scene)

    assert snapshot.schema_version == "scene_snapshot.v2"
    assert snapshot.scene_objects
    assert snapshot.interaction_slots
    assert snapshot.affordances
    assert any(item.object_type == "exit" for item in snapshot.scene_objects)
    assert any(item.action_type == "move" for item in snapshot.interaction_slots)
    assert any(item.enabled for item in snapshot.affordances)
