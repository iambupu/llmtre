from __future__ import annotations

import json

import pytest

import tools.logs.nlu_eval_check as nlu_eval_check


class _FakeNLUAgent:
    """
    功能：替代真实 NLUAgent，按输入文本返回可控解析结果。
    入参：rules（dict）：规则配置，占位保留。
    出参：_FakeNLUAgent。
    异常：无显式异常。
    """

    llm_enabled = True

    def __init__(self, rules: dict) -> None:
        self.rules = rules

    def parse(self, text: str, context: dict) -> dict | None:  # noqa: ARG002
        if text == "观察":
            return {"type": "observe", "parameters": {}}
        if text == "前往森林":
            return {"type": "move", "parameters": {"location_id": "forest_edge"}}
        if text == "无法解析":
            return None
        return {"type": "wait", "parameters": {}}


def _write_cases(tmp_path, cases: list[dict]) -> None:
    """
    功能：在临时工作目录写入 nlu_eval_check 期望的 fixture 路径。
    入参：tmp_path；cases（list[dict]）：评估样例。
    出参：None。
    异常：文件写入失败时向上抛出。
    """
    fixture_dir = tmp_path / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "nlu_eval_cases.json").write_text(
        json.dumps(cases, ensure_ascii=False),
        encoding="utf-8",
    )


def _patch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：替换评估脚本中的 NLUAgent 与规则加载函数。
    入参：monkeypatch（pytest.MonkeyPatch）：pytest 补丁器。
    出参：None。
    异常：补丁失败时由 pytest 抛出。
    """
    monkeypatch.setattr(nlu_eval_check, "NLUAgent", _FakeNLUAgent)
    monkeypatch.setattr(nlu_eval_check, "load_main_loop_rules", lambda: {"rules": True})


def test_eval_context_contains_minimal_scene_snapshot() -> None:
    """
    功能：验证评估上下文包含角色、出口、NPC 与可用动作，保证 NLU 样例有稳定语义环境。
    入参：无。
    出参：None。
    异常：断言失败表示评估上下文契约回归。
    """
    context = nlu_eval_check._eval_context()  # noqa: SLF001

    assert context["id"] == "player_01"
    assert context["scene_snapshot"]["exits"][0]["location_id"] == "forest_edge"
    assert context["scene_snapshot"]["visible_npcs"][0]["entity_id"] == "goblin_01"


def test_nlu_eval_main_prints_success_summary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """
    功能：验证全部样例通过时 main 输出 JSON 汇总且不抛 SystemExit。
    入参：tmp_path；monkeypatch；capsys。
    出参：None。
    异常：断言失败表示成功退出码/统计结构回归。
    """
    _write_cases(
        tmp_path,
        [
            {"name": "observe", "input": "观察", "expected_type": "observe"},
            {
                "name": "move",
                "input": "前往森林",
                "expected_type": "move",
                "expected_location_id": "forest_edge",
            },
        ],
    )
    monkeypatch.chdir(tmp_path)
    _patch_agent(monkeypatch)

    nlu_eval_check.main()
    output = json.loads(capsys.readouterr().out)

    assert output["total"] == 2
    assert output["passed"] == 2
    assert output["failed"] == 0
    assert output["failures"] == []


def test_nlu_eval_main_reports_failures_and_exits_one(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """
    功能：验证解析失败、类型不匹配和地点不匹配会进入 failures 并以 1 退出。
    入参：tmp_path；monkeypatch；capsys。
    出参：None。
    异常：断言失败表示失败样例统计或退出码回归。
    """
    _write_cases(
        tmp_path,
        [
            {"name": "none", "input": "无法解析", "expected_type": "observe"},
            {"name": "type_mismatch", "input": "其他", "expected_type": "observe"},
            {
                "name": "location_mismatch",
                "input": "前往森林",
                "expected_type": "move",
                "expected_location_id": "town",
            },
        ],
    )
    monkeypatch.chdir(tmp_path)
    _patch_agent(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        nlu_eval_check.main()
    output = json.loads(capsys.readouterr().out)

    assert exc_info.value.code == 1
    assert output["total"] == 3
    assert output["passed"] == 0
    assert output["failed"] == 3
    assert [item["case"] for item in output["failures"]] == [
        "none",
        "type_mismatch",
        "location_mismatch",
    ]


def test_nlu_eval_main_raises_when_fixture_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 fixture 文件缺失时 main 抛 FileNotFoundError，明确暴露基线损坏。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示缺文件异常链路回归。
    """
    monkeypatch.chdir(tmp_path)
    _patch_agent(monkeypatch)

    with pytest.raises(FileNotFoundError):
        nlu_eval_check.main()
