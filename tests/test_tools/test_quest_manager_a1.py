from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from state.models.quest import (
    EvaluatorType,
    ObjectiveEvaluator,
    QuestObjective,
    QuestStage,
    QuestStatus,
    QuestTemplate,
)
from tools.quest.quest_manager import QuestManager


class _FakeEvaluator:
    """
    功能：模拟脚本与 LLM 判定器，记录调用参数并返回可控结果。
    入参：python_result（bool）：脚本判定结果；llm_result（bool）：LLM 判定结果。
    出参：测试辅助对象。
    异常：无。
    """

    def __init__(self, *, python_result: bool = True, llm_result: bool = True) -> None:
        self.python_result = python_result
        self.llm_result = llm_result
        self.python_calls: list[tuple[str, dict[str, Any]]] = []
        self.llm_calls: list[tuple[str, str]] = []

    def evaluate_python_condition(self, condition: str, context: dict[str, Any]) -> bool:
        """
        功能：记录 Python 判定调用并返回预置结果。
        入参：condition（str）：判定表达式；context（dict[str, Any]）：判定上下文。
        出参：bool，预置脚本判定结果。
        异常：无。
        """
        self.python_calls.append((condition, context))
        return self.python_result

    def evaluate_llm_condition(self, condition: str, history: str) -> bool:
        """
        功能：记录 LLM 判定调用并返回预置结果。
        入参：condition（str）：判定提示；history（str）：历史文本。
        出参：bool，预置 LLM 判定结果。
        异常：无。
        """
        self.llm_calls.append((condition, history))
        return self.llm_result


def _init_quest_db(db_path: Path) -> None:
    """
    功能：初始化 QuestManager 测试所需最小任务表结构。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for table in ("quests_active", "quests_shadow"):
            cursor.execute(
                f"""
                CREATE TABLE {table} (
                    quest_id TEXT PRIMARY KEY,
                    current_stage_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    objectives_progress_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        conn.commit()


def _objective(
    objective_id: str,
    evaluator_type: EvaluatorType,
    condition: str,
    *,
    value: object = True,
    mandatory: bool = True,
) -> QuestObjective:
    """
    功能：构造任务目标测试数据。
    入参：objective_id（str）：目标 ID；evaluator_type（EvaluatorType）：判定类型；
        condition（str）：判定条件；value（object）：deterministic 期望值，默认 True；
        mandatory（bool）：是否必做，默认 True。
    出参：QuestObjective。
    异常：模型校验失败时抛出 pydantic.ValidationError。
    """
    return QuestObjective(
        objective_id=objective_id,
        description=f"目标 {objective_id}",
        is_mandatory=mandatory,
        evaluator=ObjectiveEvaluator(
            evaluator_type=evaluator_type,
            condition=condition,
            parameters={"value": value},
        ),
    )


def _quest_template(next_stage_id: str | None = "stage_2") -> QuestTemplate:
    """
    功能：构造两阶段任务模板，第一阶段可推进到第二阶段。
    入参：next_stage_id（str | None）：第一阶段下一阶段 ID。
    出参：QuestTemplate。
    异常：模型校验失败时抛出 pydantic.ValidationError。
    """
    return QuestTemplate(
        quest_id="quest_01",
        name="测试任务",
        description="测试状态机",
        stages=[
            QuestStage(
                stage_id="stage_1",
                name="阶段一",
                description="开始",
                objectives=[
                    _objective("mandatory", EvaluatorType.DETERMINISTIC, "has_key"),
                    _objective(
                        "optional",
                        EvaluatorType.DETERMINISTIC,
                        "talked",
                        mandatory=False,
                    ),
                ],
                next_stage_id=next_stage_id,
            ),
            QuestStage(
                stage_id="stage_2",
                name="阶段二",
                description="结束",
                objectives=[_objective("final", EvaluatorType.DETERMINISTIC, "boss_down")],
            ),
        ],
    )


def _read_quest(db_path: Path, table: str = "quests_active") -> sqlite3.Row | None:
    """
    功能：读取测试任务当前状态。
    入参：db_path（Path）：SQLite 文件路径；table（str）：任务表名，默认 active。
    出参：sqlite3.Row | None，任务行。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(f"SELECT * FROM {table} WHERE quest_id = 'quest_01'").fetchone()


def test_start_quest_writes_initial_progress_to_active_or_shadow(tmp_path: Path) -> None:
    """
    功能：验证 start_quest 会写入初始阶段和目标进度，并支持 Shadow 表。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示任务启动写入分支回归。
    """
    db_path = tmp_path / "quest.db"
    _init_quest_db(db_path)
    manager = QuestManager(str(db_path), evaluator=_FakeEvaluator())

    manager.start_quest(_quest_template())
    manager.start_quest(_quest_template(), use_shadow=True)

    active = _read_quest(db_path, "quests_active")
    shadow = _read_quest(db_path, "quests_shadow")
    assert active is not None
    assert shadow is not None
    assert active["current_stage_id"] == "stage_1"
    assert active["status"] == QuestStatus.IN_PROGRESS
    assert json.loads(active["objectives_progress_json"]) == {
        "mandatory": False,
        "optional": False,
    }
    assert shadow["current_stage_id"] == "stage_1"


def test_update_progress_returns_for_missing_or_non_active_quest(tmp_path: Path) -> None:
    """
    功能：验证任务缺失或非进行中时 update_progress 安静返回，不误写状态。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示缺失任务降级回归。
    """
    db_path = tmp_path / "quest_missing.db"
    _init_quest_db(db_path)
    manager = QuestManager(str(db_path), evaluator=_FakeEvaluator())
    template = _quest_template()

    manager.update_progress(template, {"has_key": True})
    assert _read_quest(db_path) is None

    manager.start_quest(template)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE quests_active SET status = ? WHERE quest_id = ?",
            (QuestStatus.COMPLETED, template.quest_id),
        )
        conn.commit()

    manager.update_progress(template, {"has_key": True})
    row = _read_quest(db_path)
    assert row is not None
    assert row["status"] == QuestStatus.COMPLETED


def test_update_progress_updates_partial_optional_progress(tmp_path: Path) -> None:
    """
    功能：验证只有可选目标完成时会更新进度，但不会推进阶段或完成任务。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示部分目标进度保存回归。
    """
    db_path = tmp_path / "quest_partial.db"
    _init_quest_db(db_path)
    manager = QuestManager(str(db_path), evaluator=_FakeEvaluator())
    template = _quest_template()
    manager.start_quest(template)

    manager.update_progress(template, {"has_key": False, "talked": True})

    row = _read_quest(db_path)
    assert row is not None
    assert row["current_stage_id"] == "stage_1"
    assert row["status"] == QuestStatus.IN_PROGRESS
    assert json.loads(row["objectives_progress_json"]) == {
        "mandatory": False,
        "optional": True,
    }


def test_update_progress_transitions_to_next_stage_and_completes_final(tmp_path: Path) -> None:
    """
    功能：验证必做目标完成后推进阶段，最终阶段完成后标记任务完成。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示阶段推进或完成状态回归。
    """
    db_path = tmp_path / "quest_transition.db"
    _init_quest_db(db_path)
    manager = QuestManager(str(db_path), evaluator=_FakeEvaluator())
    template = _quest_template()
    manager.start_quest(template)

    manager.update_progress(template, {"has_key": True, "talked": False})

    row = _read_quest(db_path)
    assert row is not None
    assert row["current_stage_id"] == "stage_2"
    assert json.loads(row["objectives_progress_json"]) == {"final": False}

    manager.update_progress(template, {"boss_down": True})

    completed = _read_quest(db_path)
    assert completed is not None
    assert completed["status"] == QuestStatus.COMPLETED


def test_update_progress_missing_current_or_next_stage_does_not_crash(tmp_path: Path) -> None:
    """
    功能：验证当前阶段或下一阶段模板缺失时降级返回，不抛异常。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示缺失阶段降级回归。
    """
    db_path = tmp_path / "quest_missing_stage.db"
    _init_quest_db(db_path)
    manager = QuestManager(str(db_path), evaluator=_FakeEvaluator())
    template = _quest_template(next_stage_id="missing_stage")
    manager.start_quest(template)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE quests_active SET current_stage_id = ? WHERE quest_id = ?",
            ("unknown_stage", template.quest_id),
        )
        conn.commit()
    manager.update_progress(template, {"has_key": True})
    row = _read_quest(db_path)
    assert row is not None
    assert row["current_stage_id"] == "unknown_stage"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE quests_active SET current_stage_id = ? WHERE quest_id = ?",
            ("stage_1", template.quest_id),
        )
        conn.commit()
    manager.update_progress(template, {"has_key": True})
    unchanged = _read_quest(db_path)
    assert unchanged is not None
    assert unchanged["current_stage_id"] == "stage_1"


def test_evaluate_objective_delegates_script_llm_and_unknown_type() -> None:
    """
    功能：验证目标判定会委托 Python/LLM evaluator，未知类型降级 False。
    入参：无。
    出参：None。
    异常：断言失败表示 evaluator 分发回归。
    """
    evaluator = _FakeEvaluator(python_result=True, llm_result=False)
    manager = QuestManager("unused.db", evaluator=evaluator)
    script_obj = _objective("script", EvaluatorType.PYTHON_SCRIPT, "hp > 0")
    llm_obj = _objective("llm", EvaluatorType.LLM_PROMPT, "是否完成")

    assert manager._evaluate_objective(script_obj, {"hp": 1}) is True  # noqa: SLF001
    assert manager._evaluate_objective(llm_obj, {"history": "还没完成"}) is False  # noqa: SLF001
    assert evaluator.python_calls == [("hp > 0", {"hp": 1})]
    assert evaluator.llm_calls == [("是否完成", "还没完成")]

    script_obj.evaluator.evaluator_type = "unknown"  # type: ignore[assignment]
    assert manager._evaluate_objective(script_obj, {}) is False  # noqa: SLF001
