"""
功能：覆盖 A3 分支后果摘要生成器。
"""

from __future__ import annotations

from web_api.branch_consequences import build_branch_consequence_summaries


def test_branch_consequence_summary_uses_structured_evidence() -> None:
    """
    功能：验证分支后果摘要来自 trigger/quest/physics_diff 结构化证据，而不是 final_response。
    入参：无。
    出参：None。
    异常：断言失败表示 A3 后果摘要契约回归。
    """
    payload = {
        "action_intent": {"type": "talk"},
        "physics_diff": {"state_flags_add": ["branch_report_to_watch"]},
        "trigger_events": [
            {
                "trigger_id": "talk_captain_yun",
                "memory_text": "你选择公开把证据交给守备所。",
            }
        ],
        "quest_states": [
            {
                "quest_id": "unmask_the_salt_deal",
                "status": "active",
                "current_stage_id": "report_to_watch",
                "data": {"stages_completed": ["gather_leads", "choose_approach"]},
            }
        ],
        "pack_quests": [
            {
                "quest_id": "unmask_the_salt_deal",
                "stages": [
                    {"stage_id": "gather_leads", "label": "搜集盐契线索"},
                    {"stage_id": "choose_approach", "label": "决定调查路线"},
                    {
                        "stage_id": "report_to_watch",
                        "label": "公开交给守备所",
                        "completion_condition": {
                            "a3_branch_group": "salt_deal_approach",
                            "branch_value": "report_to_watch",
                        },
                    },
                ],
            }
        ],
        "pack_triggers": [
            {
                "trigger_id": "talk_captain_yun",
                "effects": ["update_quest", "set_flag"],
                "conditions": {
                    "quest_id": "unmask_the_salt_deal",
                    "target_stage_id": "report_to_watch",
                    "a3_branch_group": "salt_deal_approach",
                    "branch_value": "report_to_watch",
                },
            }
        ],
        "final_response": "这段叙事不应作为摘要证据。",
    }

    summaries = build_branch_consequence_summaries(payload=payload, source_turn_id=3)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["source_turn_id"] == 3
    assert summary["source_action"] == "talk"
    assert summary["quest_id"] == "unmask_the_salt_deal"
    assert summary["from_stage_id"] == "choose_approach"
    assert summary["to_stage_id"] == "report_to_watch"
    assert summary["branch_path"] == "report_to_watch"
    labels = [item["label"] for item in summary["state_changes"]]
    assert any(label.startswith("任务阶段：") for label in labels)
    assert "新增状态：branch_report_to_watch" in labels
    assert "这段叙事" not in str(summary)
