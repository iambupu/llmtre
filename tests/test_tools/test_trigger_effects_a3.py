"""
功能：覆盖 A3 分支触发器效果写入 QuestRuntimeState.data 的回归测试。
"""

from state.contracts.quest import QuestRuntimeState
from state.contracts.trigger import TriggerDef
from tools.packs.trigger_effects import apply_trigger_effects


def test_update_quest_effect_persists_a3_branch_metadata() -> None:
    """
    功能：验证 update_quest effect 会把 A3 分支路线写入完整任务运行态。
    入参：无。
    出参：None。
    异常：断言失败表示分支选择无法跨回合从 QuestRuntimeState 恢复。
    """
    quest_states = [
        QuestRuntimeState(
            quest_id="unmask_the_salt_deal",
            status="active",
            current_stage_id="choose_approach",
        )
    ]
    trigger = TriggerDef(
        trigger_id="talk_captain_yun",
        type="talk",
        label="公开交证据",
        effects=["update_quest", "set_flag"],
        conditions={
            "quest_id": "unmask_the_salt_deal",
            "target_stage_id": "report_to_watch",
            "a3_branch_group": "salt_deal_approach",
            "branch_value": "report_to_watch",
            "flag": "branch_report_to_watch",
        },
    )

    physics_diff: dict[str, object] = {}
    result = apply_trigger_effects(
        trigger_events=[{"trigger_id": "talk_captain_yun"}],
        pack_triggers={"talk_captain_yun": trigger},
        physics_diff=physics_diff,
        quest_states=quest_states,
        active_character_id="player_01",
    )

    state = quest_states[0]
    assert result.changed_quest_ids == {"unmask_the_salt_deal"}
    assert state.current_stage_id == "report_to_watch"
    assert state.data["branch_path"] == "report_to_watch"
    assert state.data["branch_choices"] == [
        {
            "group": "salt_deal_approach",
            "value": "report_to_watch",
            "stage_id": "report_to_watch",
            "trigger_id": "talk_captain_yun",
        }
    ]
    assert state.data["consequence_refs"] == ["talk_captain_yun"]
    assert physics_diff["state_flags_add"] == ["branch_report_to_watch"]


def test_grant_item_effect_accepts_multiple_structured_items() -> None:
    """
    功能：验证 grant_item effect 可一次授予证据物与可消耗补给，供长回合试玩覆盖物品使用。
    入参：无。
    出参：None。
    异常：断言失败表示 grant_items 结构化参数未写入 physics_diff。
    """
    trigger = TriggerDef(
        trigger_id="inspect_hidden_crate",
        type="inspect",
        label="盐箱官封残蜡",
        effects=["grant_item"],
        conditions={
            "grant_items": [
                {"item_id": "salt_wax_rubbing", "quantity": 1},
                {"item_id": "a3_field_ration", "quantity": 1},
            ]
        },
    )
    physics_diff: dict[str, object] = {}

    apply_trigger_effects(
        trigger_events=[{"trigger_id": "inspect_hidden_crate"}],
        pack_triggers={"inspect_hidden_crate": trigger},
        physics_diff=physics_diff,
        quest_states=[],
        active_character_id="player_01",
    )

    assert physics_diff["granted_items"] == [
        {"item_id": "salt_wax_rubbing", "owner_id": "player_01", "quantity": 1},
        {"item_id": "a3_field_ration", "owner_id": "player_01", "quantity": 1},
    ]
