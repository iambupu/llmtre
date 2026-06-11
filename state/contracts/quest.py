"""
A2-Plus Quest System 契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class QuestStage(BaseModel):
    """
    功能：定义任务中的一个阶段节点。
    入参：stage_id（str）：阶段稳定 ID；label（str）：展示名；
        description（str，默认空）：阶段描述；
        triggers_on_activate（list[str]，默认空）：进入阶段时触发的触发器 key；
        triggers_on_complete（list[str]，默认空）：完成阶段时触发的触发器 key；
        completion_condition（dict[str, Any]，默认空）：阶段完成条件。
    出参：QuestStage。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    stage_id: str
    label: str
    description: str = ""
    triggers_on_activate: list[str] = Field(default_factory=list)
    triggers_on_complete: list[str] = Field(default_factory=list)
    completion_condition: dict[str, Any] = Field(default_factory=dict)


class QuestDef(BaseModel):
    """
    功能：定义一条完整任务的静态数据契约。
    入参：quest_id（str）：任务稳定 ID；title（str）：展示名；
        description（str，默认空）：任务描述；
        stages（list[QuestStage]）：阶段列表，顺序为推进主线；
        start_stage_id（str）：起始阶段 ID，必须存在于 stages 列表中。
    出参：QuestDef。
    异常：start_stage_id 不在 stages 中时抛出 ValueError，由 Pydantic 汇总为 ValidationError。
    """

    quest_id: str
    title: str
    description: str = ""
    stages: list[QuestStage]
    start_stage_id: str

    @field_validator("start_stage_id")
    @classmethod
    def validate_start_stage_exists(cls, value: str, info: Any) -> str:
        """
        功能：校验 start_stage_id 是否引用 stages 列表中存在的阶段。
        入参：value（str）：start_stage_id 字段值；info（ValidationInfo）：Pydantic 校验上下文。
        出参：str，原值返回。
        异常：value 未匹配任何 stages 中的 stage_id 时抛出 ValueError。
        """
        stages_data = info.data.get("stages")
        if stages_data is None:
            # stages 字段尚未完成校验或缺失，暂不校验引用（避免误报）
            return value
        stage_ids = {s.stage_id for s in stages_data}
        if value not in stage_ids:
            raise ValueError(
                f"start_stage_id '{value}' 不在 stages 的 stage_id 列表中: {sorted(stage_ids)}"
            )
        return value


QuestStatus = Literal["locked", "active", "completed", "failed"]


class QuestRuntimeState(BaseModel):
    """
    功能：描述单条任务在运行时的动态状态快照。
    入参：quest_id（str）：关联的任务 ID；status（QuestStatus）：当前状态；
        current_stage_id（str）：当前所在阶段 ID；
        data（dict[str, Any]，默认空）：运行期自由数据（计数器、flag 等）；
        started_at（str | None，默认 None）：ISO 时间戳，任务开始时间；
        updated_at（str | None，默认 None）：ISO 时间戳，最近状态更新时间。
    出参：QuestRuntimeState。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    quest_id: str
    status: QuestStatus
    current_stage_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    updated_at: str | None = None
