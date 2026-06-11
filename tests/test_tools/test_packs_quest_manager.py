"""
功能：覆盖 packs quest manager 的回归测试。
"""

from __future__ import annotations

import json

from tools.packs.quest_manager import (
    accept_quest,
    advance_quest_stage,
    complete_quest,
    fail_quest,
    get_quest_state,
    init_quest_states,
    load_quest_defs,
)


def _make_quest_json(tmpdir, quest_id, start_stage_id="stage_a"):
    """
    功能：提供 make quest json 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    quests_dir = tmpdir / "quests"
    quests_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "quest_id": quest_id,
        "title": f"Quest {quest_id}",
        "description": f"Desc {quest_id}",
        "stages": [
            {"stage_id": "stage_a", "label": "Stage A"},
            {"stage_id": "stage_b", "label": "Stage B"},
            {"stage_id": "stage_c", "label": "Stage C"},
        ],
        "start_stage_id": start_stage_id,
    }
    path = quests_dir / f"{quest_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _find_state(quest_id, states):
    """
    功能：提供 find state 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    for s in states:
        if s.quest_id == quest_id:
            return s
    return None


def test_load_quest_defs_loads_all_json_files(tmp_path):
    """
    功能：验证 load quest defs loads all json files 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    _make_quest_json(tmp_path, "q2")
    defs = load_quest_defs(tmp_path)
    assert len(defs) == 2
    ids = {d.quest_id for d in defs}
    assert ids == {"q1", "q2"}


def test_load_quest_defs_empty_dir_returns_empty(tmp_path):
    """
    功能：验证 load quest defs empty dir returns empty 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    defs = load_quest_defs(tmp_path)
    assert defs == []


def test_init_quest_states_all_locked(tmp_path):
    """
    功能：验证 init quest states all locked 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1", start_stage_id="stage_a")
    _make_quest_json(tmp_path, "q2", start_stage_id="stage_b")
    states = init_quest_states(tmp_path)
    assert len(states) == 2
    s1 = _find_state("q1", states)
    assert s1.status == "locked"
    assert s1.current_stage_id == "stage_a"
    s2 = _find_state("q2", states)
    assert s2.status == "locked"
    assert s2.current_stage_id == "stage_b"


def test_accept_quest_locked_to_active(tmp_path):
    """
    功能：验证 accept quest locked to active 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    new_states = accept_quest("q1", states)
    s = _find_state("q1", new_states)
    assert s.status == "active"
    assert _find_state("q1", states).status == "locked"


def test_accept_quest_unknown_id(tmp_path):
    """
    功能：验证 accept quest unknown id 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    new_states = accept_quest("nonexistent", states)
    assert new_states == states


def test_accept_quest_already_active(tmp_path):
    """
    功能：验证 accept quest already active 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    again = accept_quest("q1", active_states)
    assert again == active_states


def test_accept_quest_completed(tmp_path):
    """
    功能：验证 accept quest completed 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    completed_states = complete_quest("q1", active_states)
    again = accept_quest("q1", completed_states)
    assert again == completed_states


def test_accept_quest_failed(tmp_path):
    """
    功能：验证 accept quest failed 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    failed_states = fail_quest("q1", active_states)
    again = accept_quest("q1", failed_states)
    assert again == failed_states


def test_advance_quest_stage_active_to_active(tmp_path):
    """
    功能：验证 advance quest stage active to active 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    new_states = advance_quest_stage("q1", "stage_b", active_states)
    s = _find_state("q1", new_states)
    assert s.status == "active"
    assert s.current_stage_id == "stage_b"
    assert "stage_a" in s.data.get("stages_completed", [])


def test_advance_quest_stage_unknown_quest(tmp_path):
    """
    功能：验证 advance quest stage unknown quest 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    new_states = advance_quest_stage("nonexistent", "stage_b", active_states)
    assert new_states == active_states


def test_advance_quest_stage_locked_rejected(tmp_path):
    """
    功能：验证 advance quest stage locked rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    new_states = advance_quest_stage("q1", "stage_b", states)
    assert new_states == states


def test_advance_quest_stage_completed_rejected(tmp_path):
    """
    功能：验证 advance quest stage completed rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    completed_states = complete_quest("q1", active_states)
    new_states = advance_quest_stage("q1", "stage_c", completed_states)
    assert new_states == completed_states


def test_advance_quest_stage_failed_rejected(tmp_path):
    """
    功能：验证 advance quest stage failed rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    failed_states = fail_quest("q1", active_states)
    new_states = advance_quest_stage("q1", "stage_c", failed_states)
    assert new_states == failed_states


def test_advance_quest_stage_invalid_stage(tmp_path):
    """
    功能：验证 advance quest stage invalid stage 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    new_states = advance_quest_stage("q1", "stage_z", active_states)
    assert _find_state("q1", new_states).current_stage_id == "stage_z"


def test_complete_quest_active_to_completed(tmp_path):
    """
    功能：验证 complete quest active to completed 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    new_states = complete_quest("q1", active_states)
    s = _find_state("q1", new_states)
    assert s.status == "completed"
    assert s.updated_at is not None


def test_complete_quest_locked_rejected(tmp_path):
    """
    功能：验证 complete quest locked rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    new_states = complete_quest("q1", states)
    assert new_states == states


def test_complete_quest_failed_rejected(tmp_path):
    """
    功能：验证 complete quest failed rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    failed_states = fail_quest("q1", active_states)
    new_states = complete_quest("q1", failed_states)
    assert new_states == failed_states


def test_fail_quest_active_to_failed(tmp_path):
    """
    功能：验证 fail quest active to failed 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    new_states = fail_quest("q1", active_states)
    s = _find_state("q1", new_states)
    assert s.status == "failed"
    assert s.updated_at is not None


def test_fail_quest_locked_rejected(tmp_path):
    """
    功能：验证 fail quest locked rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    new_states = fail_quest("q1", states)
    assert new_states == states


def test_fail_quest_completed_rejected(tmp_path):
    """
    功能：验证 fail quest completed rejected 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    active_states = accept_quest("q1", states)
    completed_states = complete_quest("q1", active_states)
    new_states = fail_quest("q1", completed_states)
    assert new_states == completed_states


def test_get_quest_state_found(tmp_path):
    """
    功能：验证 get quest state found 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    s = get_quest_state("q1", states)
    assert s is not None
    assert s.quest_id == "q1"
    assert s.status == "locked"


def test_get_quest_state_not_found(tmp_path):
    """
    功能：验证 get quest state not found 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    s = get_quest_state("nonexistent", states)
    assert s is None


def test_immutability_original_states_unchanged(tmp_path):
    """
    功能：验证 immutability original states unchanged 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    _make_quest_json(tmp_path, "q1")
    states = init_quest_states(tmp_path)
    original_status = _find_state("q1", states).status
    _ = accept_quest("q1", states)
    assert _find_state("q1", states).status == original_status
