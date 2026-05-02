"""
最小澄清 Agent。
"""

from __future__ import annotations

from typing import Any

from state.contracts.agent import AgentEnvelope


class ClarifierAgent:
    """
    功能：把动作缺参或意图不明转换为玩家可理解的澄清问题。
    入参：无。
    出参：ClarifierAgent 实例。
    异常：构造阶段不抛业务异常。
    """

    def clarify(self, envelope: AgentEnvelope) -> AgentEnvelope:
        """
        功能：基于场景对象、交互槽和候选动作生成澄清响应。
        入参：envelope（AgentEnvelope）：kind 应为 clarify.request，payload 包含 action_intent、
            validation_errors、scene_snapshot、affordances。
        出参：AgentEnvelope，kind 为 clarify.response，payload 包含 clarification_question、
            failure_reason、suggested_next_step、candidate_inputs。
        异常：不抛业务异常；缺字段时按通用澄清问题降级。
        """
        payload = envelope.payload
        action_raw = payload.get("action_intent")
        scene_raw = payload.get("scene_snapshot")
        action = action_raw if isinstance(action_raw, dict) else {}
        scene = scene_raw if isinstance(scene_raw, dict) else {}
        action_type = str(action.get("type") or "")
        question = self._build_question(action_type, scene)
        candidate_inputs = self._candidate_inputs(scene)
        failure_reason = self._failure_reason(payload)
        suggested_next_step = candidate_inputs[0] if candidate_inputs else "观察周围"
        return AgentEnvelope(
            trace_id=envelope.trace_id,
            turn_id=envelope.turn_id,
            sender="clarifier",
            recipient=envelope.sender,
            kind="clarify.response",
            payload={
                "clarification_question": question,
                "failure_reason": failure_reason,
                "suggested_next_step": suggested_next_step,
                "candidate_inputs": candidate_inputs,
            },
        )

    def _build_question(self, action_type: str, scene: dict[str, Any]) -> str:
        """
        功能：按动作类型生成最小澄清问题。
        入参：action_type（str）：候选动作类型；scene（dict[str, Any]）：场景快照。
        出参：str，中文澄清问题。
        异常：不抛异常，字段缺失时使用通用问题。
        """
        if action_type == "move":
            labels = self._labels(scene.get("exits"), "label", "location_id")
            return (
                f"你想往哪个方向走？当前可选出口：{'、'.join(labels)}。"
                if labels else "你想往哪里走？当前没有明确出口。"
            )
        if action_type in {"talk", "attack"}:
            labels = self._labels(scene.get("visible_npcs"), "name", "entity_id")
            verb = "攻击" if action_type == "attack" else "交谈"
            return (
                f"你想{verb}哪个目标？当前可见目标：{'、'.join(labels)}。"
                if labels else f"你想{verb}谁？当前没有明确可见目标。"
            )
        if action_type == "use_item":
            return "你想使用哪个物品？"
        if action_type in {"inspect", "interact"}:
            labels = self._labels(scene.get("scene_objects"), "label", "object_id")
            return (
                f"你想处理哪个对象？当前可选对象：{'、'.join(labels[:6])}。"
                if labels else "你想检查或互动哪个对象？"
            )
        return "我还没有理解你的行动，你想观察、移动、交谈，还是检查某个对象？"

    def _candidate_inputs(self, scene: dict[str, Any]) -> list[str]:
        """
        功能：从 affordances 中提取可直接提交的候选输入。
        入参：scene（dict[str, Any]）：场景快照。
        出参：list[str]，最多四条候选输入。
        异常：不抛异常；字段缺失时返回保底候选。
        """
        raw_affordances = scene.get("affordances", [])
        candidates: list[str] = []
        if isinstance(raw_affordances, list):
            for item in raw_affordances:
                if not isinstance(item, dict) or not bool(item.get("enabled", False)):
                    continue
                text = str(item.get("user_input") or item.get("label") or "").strip()
                if text and text not in candidates:
                    candidates.append(text)
                if len(candidates) >= 4:
                    break
        candidates.extend(["观察周围", "检查周围", "等待片刻", "短暂休息"])
        result: list[str] = []
        for item in candidates:
            if item not in result:
                result.append(item)
            if len(result) >= 4:
                break
        return result

    def _failure_reason(self, payload: dict[str, Any]) -> str:
        """
        功能：从验证错误或动作缺参状态中提炼失败原因。
        入参：payload（dict[str, Any]）：澄清请求负载。
        出参：str，失败原因。
        异常：不抛异常，缺失时返回通用原因。
        """
        errors = payload.get("validation_errors")
        if isinstance(errors, list) and errors:
            return "；".join(str(item) for item in errors)
        return "行动信息还不够明确。"

    def _labels(self, value: Any, primary_key: str, fallback_key: str) -> list[str]:
        """
        功能：从对象列表中提取展示标签。
        入参：value（Any）：候选列表；primary_key/fallback_key（str）：字段名。
        出参：list[str]。
        异常：不抛异常，非法项跳过。
        """
        if not isinstance(value, list):
            return []
        labels: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get(primary_key) or item.get(fallback_key) or "").strip()
            if label:
                labels.append(label)
        return labels
