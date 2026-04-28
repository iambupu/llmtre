import json
import os
import sqlite3
from typing import Any

from state.models.quest import EvaluatorType, QuestObjective, QuestStatus, QuestTemplate
from tools.sandbox.script_evaluator import ScriptEvaluator

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")

class QuestManager:
    """任务管理器：负责任务状态机的维护、判定逻辑触发和进度更新"""

    def __init__(
        self,
        db_path: str = DB_PATH,
        evaluator: ScriptEvaluator | None = None,
    ) -> None:
        """
        功能：初始化对象状态与依赖。
        入参：db_path；evaluator。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path
        self.evaluator = evaluator or ScriptEvaluator()

    def _get_conn(self) -> sqlite3.Connection:
        """
        功能：创建任务读写流程使用的 SQLite 连接。
        入参：无。
        出参：sqlite3.Connection，提交与回滚由调用方控制。
        异常：数据库不可用时抛出 sqlite3.Error，由上层决定降级策略。
        """
        return sqlite3.connect(self.db_path)

    def start_quest(self, quest: QuestTemplate, use_shadow: bool = False) -> None:
        """
        功能：开始一个新任务。
        入参：quest；use_shadow。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "quests_shadow" if use_shadow else "quests_active"
        initial_stage = quest.stages[0].stage_id
        # 初始化所有 Objective 进度为 False
        progress = {obj.objective_id: False for obj in quest.stages[0].objectives}

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT OR REPLACE INTO {table} (
                    quest_id,
                    current_stage_id,
                    status,
                    objectives_progress_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    quest.quest_id,
                    initial_stage,
                    QuestStatus.IN_PROGRESS,
                    json.dumps(progress),
                ),
            )
            conn.commit()

    def update_progress(
        self,
        quest_template: QuestTemplate,
        context: dict[str, Any],
        use_shadow: bool = False,
    ) -> None:
        """
        功能：全量检查当前活跃阶段的所有目标，并推进状态机。
        入参：quest_template；context；use_shadow。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        table = "quests_shadow" if use_shadow else "quests_active"

        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table} WHERE quest_id = ?", (quest_template.quest_id,))
            row = cursor.fetchone()
            if not row or row['status'] != QuestStatus.IN_PROGRESS:
                return

            current_stage_id = row['current_stage_id']
            progress = json.loads(row['objectives_progress_json'])

            # 找到当前阶段模板
            current_stage = next(
                (stage for stage in quest_template.stages if stage.stage_id == current_stage_id),
                None,
            )
            if not current_stage:
                return

            # 判定每个未完成的目标
            any_changed = False
            for obj in current_stage.objectives:
                if not progress.get(obj.objective_id, False):
                    if self._evaluate_objective(obj, context):
                        progress[obj.objective_id] = True
                        any_changed = True

            if any_changed:
                # 检查是否所有必须目标都已完成
                all_done = all(
                    progress.get(obj.objective_id, False)
                    for obj in current_stage.objectives
                    if obj.is_mandatory
                )

                if all_done:
                    # 尝试进入下一阶段
                    next_stage_id = current_stage.next_stage_id
                    if next_stage_id:
                        self._transition_to_stage(cursor, table, quest_template, next_stage_id)
                    else:
                        # 任务完成
                        cursor.execute(
                            f"""
                            UPDATE {table}
                            SET status = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE quest_id = ?
                            """,
                            (QuestStatus.COMPLETED, quest_template.quest_id),
                        )
                else:
                    # 仅更新进度
                    cursor.execute(
                        f"""
                        UPDATE {table}
                        SET objectives_progress_json = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE quest_id = ?
                        """,
                        (json.dumps(progress), quest_template.quest_id),
                    )
                conn.commit()

    def _evaluate_objective(self, objective: QuestObjective, context: dict[str, Any]) -> bool:
        """
        功能：调用 Evaluator 进行判定。
        入参：objective；context。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        ev = objective.evaluator
        if ev.evaluator_type == EvaluatorType.PYTHON_SCRIPT:
            return self.evaluator.evaluate_python_condition(ev.condition, context)
        elif ev.evaluator_type == EvaluatorType.LLM_PROMPT:
            return self.evaluator.evaluate_llm_condition(ev.condition, context.get("history", ""))
        elif ev.evaluator_type == EvaluatorType.DETERMINISTIC:
            # 简单数值对比：context 需提供对应变量
            # TODO: 实现更通用的 deterministic 比较
            return context.get(ev.condition) == ev.parameters.get("value")
        return False

    def _transition_to_stage(
        self,
        cursor: sqlite3.Cursor,
        table: str,
        quest_template: QuestTemplate,
        next_stage_id: str,
    ) -> None:
        """
        功能：执行阶段流转。
        入参：cursor（sqlite3.Cursor）：当前事务游标；table（str）：目标任务表名；
            quest_template（QuestTemplate）：任务模板；
            next_stage_id（str）：下一阶段标识。
        出参：None。
        异常：阶段不存在时静默返回（降级为不推进）；SQL 异常由调用方事务处理。
        """
        next_stage = next((s for s in quest_template.stages if s.stage_id == next_stage_id), None)
        if not next_stage:
            return

        new_progress = {obj.objective_id: False for obj in next_stage.objectives}
        cursor.execute(f"""
            UPDATE {table}
            SET current_stage_id = ?, objectives_progress_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE quest_id = ?
        """, (next_stage_id, json.dumps(new_progress), quest_template.quest_id))
