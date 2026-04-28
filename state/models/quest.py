from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class QuestStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class EvaluatorType(StrEnum):
    DETERMINISTIC = "deterministic"  # 简单数值对比
    LLM_PROMPT = "llm_prompt"        # LLM 语义判定
    PYTHON_SCRIPT = "python_script"  # 自定义脚本判定

class ObjectiveEvaluator(BaseModel):
    """任务目标判定器契约"""
    evaluator_type: EvaluatorType = Field(..., description="判定类型")
    condition: str = Field(..., description="判定具体条件 (SQL/Prompt/Python Code)")
    parameters: dict[str, Any] = Field(default_factory=dict, description="判定器附加参数")

class QuestObjective(BaseModel):
    """任务原子目标"""
    objective_id: str = Field(..., description="目标 ID")
    description: str = Field(..., description="目标描述")
    is_mandatory: bool = Field(default=True, description="是否为必须完成")
    evaluator: ObjectiveEvaluator = Field(..., description="判定逻辑")
    is_completed: bool = Field(default=False, description="当前是否已达成")

class QuestStage(BaseModel):
    """任务阶段契约 (状态机节点)"""
    stage_id: str = Field(..., description="阶段 ID")
    name: str = Field(..., description="阶段名称")
    description: str = Field(..., description="阶段背景描述")
    objectives: list[QuestObjective] = Field(..., description="该阶段需要达成的目标列表")
    next_stage_id: str | None = Field(None, description="默认下一阶段 ID")
    branching_logic: dict[str, str] | None = Field(
        None,
        description="分支逻辑：判定结果 -> 目标 Stage ID",
    )

class QuestTemplate(BaseModel):
    """剧本/任务模板核心契约"""
    quest_id: str = Field(..., description="剧本唯一标识符")
    name: str = Field(..., description="剧本名称")
    description: str = Field(..., description="剧本概要描述")
    stages: list[QuestStage] = Field(..., description="阶段列表")
    prerequisites: dict[str, Any] = Field(default_factory=dict, description="任务开启的前置要求")
    rewards: list[dict[str, Any]] = Field(default_factory=list, description="任务完成奖励")
    time_limit_minutes: int | None = Field(None, description="限时完成时间 (分钟)")
