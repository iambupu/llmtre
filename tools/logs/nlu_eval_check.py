"""
NLU 样例评估脚本。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.nlu_agent import NLUAgent
from game_workflows.main_loop_config import load_main_loop_rules


def _eval_context() -> dict[str, Any]:
    """
    功能：构造最小 NLU 评估场景快照。
    入参：无。
    出参：dict[str, Any]，包含角色 ID 与 scene_snapshot。
    异常：不抛异常。
    """
    return {
        "id": "player_01",
        "scene_snapshot": {
            "current_location": {"id": "unknown", "name": "无名道路", "description": ""},
            "exits": [
                {
                    "direction": "forward",
                    "location_id": "forest_edge",
                    "label": "森林边缘",
                    "aliases": ["森林", "前方", "路上", "继续"],
                }
            ],
            "visible_npcs": [{"entity_id": "goblin_01", "name": "瘦弱的地精"}],
            "visible_items": [],
            "active_quests": [],
            "recent_memory": "",
            "available_actions": ["observe", "wait", "move", "talk"],
            "suggested_actions": ["看看周围", "继续前进"],
        },
    }


def main() -> None:
    """
    功能：执行 NLU fixtures 离线评估并输出通过率。
    入参：无。
    出参：None，结果打印到 stdout。
    异常：fixture 文件缺失或 JSON 解析失败时向上抛出，提示评估基线损坏。
    """
    cases = json.loads(Path("tests/fixtures/nlu_eval_cases.json").read_text(encoding="utf-8"))
    agent = NLUAgent(rules=load_main_loop_rules())
    agent.llm_enabled = False
    passed = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        parsed = agent.parse(str(case["input"]), context=_eval_context())
        if parsed is None:
            failures.append({"case": case["name"], "parsed": parsed})
            continue
        ok = parsed.get("type") == case.get("expected_type")
        expected_location_id = case.get("expected_location_id")
        if ok and expected_location_id:
            ok = parsed.get("parameters", {}).get("location_id") == expected_location_id
        if ok:
            passed += 1
        else:
            failures.append({"case": case["name"], "parsed": parsed})
    print(
        json.dumps(
            {
                "total": len(cases),
                "passed": passed,
                "failed": len(failures),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
