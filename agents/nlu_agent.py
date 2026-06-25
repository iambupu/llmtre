"""
意图解析子智能体逻辑 (Natural Language Understanding)
负责自然语言到 JSON 动作的降维。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from agents.nlu_schema import normalize_action_candidate
from config.agent_model_loader import get_agent_model_binding
from game_workflows.main_loop_config import load_main_loop_rules

logger = logging.getLogger("Agent.NLU")


class NLUAgent:
    """自然语言理解智能体。"""

    def __init__(
        self,
        rules: dict[str, Any] | None = None,
        model_binding_key: str = "agents.nlu",
    ):
        """
        功能：初始化 NLU 智能体，并读取其模型绑定配置；本阶段只保留绑定信息，不启用真实 LLM。
        入参：rules（dict[str, Any] | None）：主循环规则配置；为 `None` 时自动从配置文件加载。
        入参：model_binding_key（str）：Agent 模型绑定键，默认值为 `agents.nlu`。
        出参：无显式返回值；实例初始化后会暴露 `model_binding` 只读配置快照。
        异常：规则文件或模型配置解析异常默认向上抛出；若绑定项缺失，则内部按保守默认值降级，不中断初始化。
        """
        loaded_rules = rules if rules is not None else load_main_loop_rules()
        self.nlu_rules = loaded_rules.get("nlu", {})
        self.model_binding_key = model_binding_key
        self.model_binding = get_agent_model_binding(model_binding_key)
        self.llm_enabled = bool(self.model_binding.get("enabled", False))
        self.llm_config = dict(self.model_binding.get("llm_config") or {})
        self.llm_timeout_seconds = int(self.model_binding.get("timeout_seconds", 5))

    def parse(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        功能：将玩家输入解析为最小结构化动作；优先使用场景快照
        exits aliases/label/direction 匹配移动目标，并检查通用移动关键词，
        避免产生 unknown 地点（A2-Plus 场景别名优先匹配）。
        入参：user_input（str）：玩家自然语言，空白输入返回 None；
            context（dict[str, Any] | None）：角色与场景上下文，可包含 id、scene_snapshot。
        出参：dict[str, Any] | None，识别成功返回候选动作 JSON，失败返回 None。
        异常：当前实现不主动抛业务异常；上下文字段缺失时按 None/空快照降级。
        """
        normalized = user_input.strip().lower()
        if not normalized:
            return None

        actor_id, scene_snapshot = self._extract_parse_context(context)
        action_keywords = self.nlu_rules.get("action_keywords", {})
        rule_candidate = self._parse_rule_candidate(
            normalized,
            user_input,
            actor_id,
            scene_snapshot,
            action_keywords,
        )
        if rule_candidate is not None:
            return rule_candidate

        if self.llm_enabled:
            return self._parse_with_llm(user_input, actor_id, scene_snapshot)
        return None

    def _extract_parse_context(
        self,
        context: dict[str, Any] | None,
    ) -> tuple[str | None, Any]:
        """
        功能：从主循环上下文中提取 NLU 需要的最小只读字段。
        入参：context（dict[str, Any] | None）：角色与场景上下文，可包含 id 与 scene_snapshot。
        出参：tuple[str | None, Any]，依次为 actor_id 与 scene_snapshot。
        异常：不抛异常；缺失字段按 None 降级，避免 NLU 影响主循环错误处理。
        """
        actor_id = context["id"] if context and "id" in context else None
        scene_snapshot = context.get("scene_snapshot") if context else None
        return actor_id, scene_snapshot

    def _parse_rule_candidate(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：按固定优先级执行确定性规则解析，失败时交由 parse 决定是否启用 LLM 兜底。
        入参：normalized（str）：已归一化玩家输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：当前场景快照；
            action_keywords（dict[str, Any]）：规则关键词配置。
        出参：dict[str, Any] | None，规则命中返回候选动作，否则返回 None。
        异常：不抛异常；各分支内部字段缺失均按已有降级逻辑处理。
        """
        sandbox_action = self._parse_sandbox_control_action(
            normalized,
            user_input,
            actor_id,
            action_keywords,
            "commit_sandbox",
        )
        if sandbox_action is not None:
            return sandbox_action

        sandbox_action = self._parse_sandbox_control_action(
            normalized,
            user_input,
            actor_id,
            action_keywords,
            "discard_sandbox",
        )
        if sandbox_action is not None:
            return sandbox_action

        explicit_inspect_action = (
            self._parse_inspect_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            if self._matches_action(normalized, action_keywords, "inspect")
            else None
        )

        return (
            self._parse_skill_action(normalized, user_input, actor_id, action_keywords)
            or self._parse_use_item_action(normalized, user_input, actor_id, action_keywords)
            or self._parse_attack_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            or explicit_inspect_action
            or self._parse_talk_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            or self._parse_move_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            or self._parse_inspect_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            or self._parse_interact_action(
                normalized,
                user_input,
                actor_id,
                scene_snapshot,
                action_keywords,
            )
            or self._parse_simple_self_action(
                normalized,
                user_input,
                actor_id,
                action_keywords,
            )
        )

    def _parse_skill_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析玩家主动发动技能的动作，优先于“使用物品”处理，避免“使用专注技能”被误判为物品。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 skill 候选，否则返回 None。
        异常：不抛异常；技能 ID 缺失时使用配置默认值，后续结算按配置降级。
        """
        if not self._matches_action(normalized, action_keywords, "skill"):
            return None
        return self._finalize_candidate(
            {
                "type": "skill",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {
                    "skill_id": self._extract_skill_id(normalized),
                    "intent": self._extract_intent(normalized, "skill"),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_sandbox_control_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        action_keywords: dict[str, Any],
        action_type: str,
    ) -> dict[str, Any] | None:
        """
        功能：解析沙盒并入/回滚控制动作，保持其优先级高于普通玩法动作。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；action_keywords（dict[str, Any]）：关键词配置；
            action_type（str）：commit_sandbox 或 discard_sandbox。
        出参：dict[str, Any] | None，命中返回控制动作，否则返回 None。
        异常：不抛异常；最终候选由 _finalize_candidate 统一校验。
        """
        if not self._matches_action(normalized, action_keywords, action_type):
            return None
        return self._finalize_candidate(
            {
                "type": action_type,
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {},
            },
            user_input,
            actor_id,
        )

    def _parse_use_item_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析使用物品动作，避免其被后续 inspect/interact 泛规则截获。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 use_item 候选，否则返回 None。
        异常：不抛异常；物品 ID 缺失时保留 None 交由主循环校验或澄清。
        """
        if not self._matches_action(normalized, action_keywords, "use_item"):
            return None
        return self._finalize_candidate(
            {
                "type": "use_item",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {
                    "item_id": self._extract_item_id(normalized),
                    "intent": self._extract_intent(normalized, "use_item"),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_attack_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析攻击动作，优先从当前场景目标解析 target_id，再回退全局别名。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：场景快照；
            action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 attack 候选，否则返回 None。
        异常：不抛异常；目标缺失时返回 target_id=None，由主循环处理不明确目标。
        """
        if not self._matches_action(normalized, action_keywords, "attack"):
            return None
        return self._finalize_candidate(
            {
                "type": "attack",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_scene_target_id(
                    normalized,
                    scene_snapshot,
                    {"attack"},
                )
                or self._extract_target_id(normalized),
                "parameters": {"manner": self._extract_manner(normalized)},
            },
            user_input,
            actor_id,
        )

    def _parse_talk_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析交谈动作，场景 NPC 与 talk 交互别名优先于全局关键词。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：场景快照；
            action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 talk 候选，否则返回 None。
        异常：不抛异常；topic/manner 缺失按空值降级。
        """
        if not (
            self._any_scene_alias_matches(normalized, scene_snapshot, ["visible_npcs"])
            or self._any_scene_alias_matches(
                normalized,
                scene_snapshot,
                ["interactables"],
                kinds={"talk"},
            )
            or self._matches_action(normalized, action_keywords, "talk")
        ):
            return None
        return self._finalize_candidate(
            {
                "type": "talk",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_scene_target_id(
                    normalized,
                    scene_snapshot,
                    {"talk"},
                )
                or self._extract_target_id(normalized),
                "parameters": {
                    "topic": self._extract_topic(normalized),
                    "manner": self._extract_manner(normalized),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_move_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析移动动作，优先使用当前场景 exits 别名解析目标地点。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：场景快照；
            action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 move 候选，否则返回 None。
        异常：不抛异常；地点解析失败时 location_id=None，由主循环澄清。
        """
        if not (
            self._any_scene_alias_matches(normalized, scene_snapshot, ["exits"])
            or self._matches_action(normalized, action_keywords, "move")
        ):
            return None
        return self._finalize_candidate(
            {
                "type": "move",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": None,
                "parameters": {
                    "location_id": self._extract_location_id(normalized, scene_snapshot),
                    "manner": self._extract_manner(normalized),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_inspect_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析检查/观察对象动作，优先匹配当前场景 inspect/observe 交互对象别名。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：场景快照；
            action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 inspect 候选，否则返回 None。
        异常：不抛异常；object_hint 为空时保留 None 交由后续校验。
        """
        if not (
            self._any_scene_alias_matches(
                normalized,
                scene_snapshot,
                ["interactables"],
                kinds={"inspect", "observe"},
            )
            or self._matches_action(normalized, action_keywords, "inspect")
        ):
            return None
        return self._finalize_candidate(
            {
                "type": "inspect",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_target_id(normalized),
                "parameters": {
                    "intent": self._extract_intent(normalized, "inspect"),
                    "object_hint": self._extract_object_hint(normalized, scene_snapshot),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_interact_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析泛交互动作，作为具体 talk/inspect/move 之后的兜底规则。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；scene_snapshot（Any）：场景快照；
            action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回 interact 候选，否则返回 None。
        异常：不抛异常；目标缺失时按 None 降级。
        """
        if not (
            self._any_scene_alias_matches(
                normalized,
                scene_snapshot,
                ["interactables"],
            )
            or self._matches_action(normalized, action_keywords, "interact")
        ):
            return None
        return self._finalize_candidate(
            {
                "type": "interact",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_target_id(normalized),
                "parameters": {
                    "intent": self._extract_intent(normalized, "interact"),
                    "object_hint": self._extract_object_hint(normalized, scene_snapshot),
                },
            },
            user_input,
            actor_id,
        )

    def _parse_simple_self_action(
        self,
        normalized: str,
        user_input: str,
        actor_id: str | None,
        action_keywords: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        功能：解析 rest/wait/observe 这类只作用于当前角色或当前场景的基础动作。
        入参：normalized（str）：已归一化输入；user_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID；action_keywords（dict[str, Any]）：关键词配置。
        出参：dict[str, Any] | None，命中返回基础动作，否则返回 None。
        异常：_build_self_action 理论失败时抛 RuntimeError，保持原基础动作构造契约。
        """
        for action_type in ("rest", "wait", "observe"):
            if self._matches_action(normalized, action_keywords, action_type):
                return self._build_self_action(action_type, user_input, actor_id)
        return None

    def _finalize_candidate(
        self,
        payload: dict[str, Any],
        raw_input: str,
        actor_id: str | None,
    ) -> dict[str, Any] | None:
        """
        功能：对规则层候选动作执行统一 schema 强校验。
        入参：payload（dict[str, Any]）：候选动作；raw_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID。
        出参：dict[str, Any] | None，校验成功返回标准动作，失败返回 None。
        异常：校验异常由 `normalize_action_candidate` 捕获并降级为 None。
        """
        return normalize_action_candidate(payload, raw_input=raw_input, actor_id=actor_id)

    def _parse_with_llm(
        self,
        user_input: str,
        actor_id: str | None,
        scene_snapshot: Any,
    ) -> dict[str, Any] | None:
        """
        功能：调用本地 LLM 生成候选动作 JSON；仅作为语义兜底，不做合法性裁决。
        入参：user_input（str）：玩家原文；actor_id（str | None）：当前角色 ID；
            scene_snapshot（Any）：当前场景快照，用于限制模型候选目标。
        出参：dict[str, Any] | None，解析到支持动作时返回候选动作，否则返回 None。
        异常：网络、协议、JSON 解析异常均内部捕获并记录日志，降级为 None。
        """
        provider = str(self.llm_config.get("provider", "")).lower()
        if provider != "ollama":
            logger.warning(
                "NLU LLM provider=%s 不受支持，回退规则失败结果。",
                provider or "unknown",
            )
            return None
        model = str(self.llm_config.get("model", "")).strip()
        if not model:
            logger.warning("NLU LLM 未配置 model，回退规则失败结果。")
            return None

        base_url = str(self.llm_config.get("base_url", "http://localhost:11434")).rstrip("/")
        temperature = float(self.llm_config.get("temperature", 0.0))
        prompt = self._build_llm_prompt(user_input, scene_snapshot)
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        request = urllib.request.Request(
            url=f"{base_url}/api/generate",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.llm_timeout_seconds) as response:
                response_text = response.read().decode("utf-8")
                payload = json.loads(response_text)
            if not isinstance(payload, dict):
                self._log_llm_failure(
                    "invalid_payload_type",
                    f"payload_type={type(payload).__name__}",
                    base_url=base_url,
                    model=model,
                    response_preview=str(payload)[:300],
                )
                return None
            raw_text = str(payload.get("response", "")).strip()
            if not raw_text:
                self._log_llm_failure(
                    "empty_response",
                    "ollama response 字段为空",
                    base_url=base_url,
                    model=model,
                    response_preview=response_text[:300],
                )
                return None
            candidate = self._load_llm_action_json(raw_text)
            normalized = self._normalize_llm_action(candidate, user_input, actor_id)
            if normalized is None:
                self._log_llm_failure(
                    "schema_validation_failed",
                    "模型输出无法通过 NLUActionCandidate 校验",
                    base_url=base_url,
                    model=model,
                    response_preview=raw_text[:300],
                )
            return normalized
        except urllib.error.HTTPError as error:
            self._log_llm_failure(
                "http_error",
                f"status={error.code} reason={error.reason}",
                base_url=base_url,
                model=model,
                response_preview=self._read_http_error_body(error),
            )
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            self._log_llm_failure(
                "url_error",
                f"reason_type={type(reason).__name__} reason={reason!r}",
                base_url=base_url,
                model=model,
            )
        except TimeoutError as error:
            self._log_llm_failure(
                "timeout",
                repr(error),
                base_url=base_url,
                model=model,
            )
        except json.JSONDecodeError as error:
            self._log_llm_failure(
                "json_decode_error",
                f"line={error.lineno} col={error.colno} msg={error.msg}",
                base_url=base_url,
                model=model,
            )
        except Exception as error:  # noqa: BLE001
            self._log_llm_failure(
                "unexpected_error",
                f"{type(error).__name__}: {error}",
                base_url=base_url,
                model=model,
            )
        return None

    def _log_llm_failure(
        self,
        reason: str,
        detail: str,
        *,
        base_url: str,
        model: str,
        response_preview: str = "",
    ) -> None:
        """
        功能：记录 NLU LLM 调用失败的可诊断上下文。
        入参：reason（str）：失败分类；detail（str）：异常细节；
            base_url（str）：请求目标服务；model（str）：模型名；
            response_preview（str，默认空）：响应片段，最多记录 300 字符。
        出参：None。
        异常：日志写入失败由 logging 内部处理；本函数不主动抛异常。
        """
        logger.warning(
            (
                "NLU LLM 调用失败，已降级为未识别动作: reason=%s detail=%s "
                "provider=ollama model=%s base_url=%s timeout=%ss binding=%s "
                "response_preview=%r"
            ),
            reason,
            detail,
            model,
            base_url,
            self.llm_timeout_seconds,
            self.model_binding_key,
            response_preview[:300],
        )

    def _read_http_error_body(self, error: urllib.error.HTTPError) -> str:
        """
        功能：读取 HTTPError 响应体片段，帮助判断模型不存在、接口错误等服务端问题。
        入参：error（urllib.error.HTTPError）：urllib 抛出的 HTTP 错误。
        出参：str，最多 300 字符的响应体片段；读取失败返回错误描述。
        异常：内部捕获响应体读取异常，避免日志增强逻辑影响主流程降级。
        """
        try:
            return error.read(300).decode("utf-8", errors="replace")
        except Exception as read_error:  # noqa: BLE001
            return f"<failed to read error body: {read_error}>"

    def _build_llm_prompt(self, user_input: str, scene_snapshot: Any) -> str:
        """
        功能：构建 NLU 候选动作提示词，明确禁止模型做数值结算或规则裁决。
        入参：user_input（str）：玩家输入；scene_snapshot（Any）：当前场景快照。
        出参：str，发送给 LLM 的提示词。
        异常：JSON 序列化失败时向上抛出，由调用方捕获并降级。
        """
        compact_scene = scene_snapshot if isinstance(scene_snapshot, dict) else {}
        return (
            "你是 TRPG NLU，只把玩家输入转成候选动作 JSON，不做数值结算、不判定成败。"
            "动作 type 只能是 observe, wait, rest, move, talk, inspect, "
            "use_item, attack, interact。"
            "输出字段必须包含 type, target_id, parameters, confidence, "
            "needs_clarification, clarification_question。"
            "如果是移动，parameters.location_id 必须优先来自 scene_snapshot.exits。"
            "如果目标、方向或对象不明确，设置 needs_clarification=true 并提出中文澄清问题。"
            "只输出 JSON 对象，不要解释。"
            f"\n玩家输入: {user_input}"
            f"\nscene_snapshot: {json.dumps(compact_scene, ensure_ascii=False)}"
            '\nJSON格式: {"type":"observe","target_id":null,"parameters":{},'
            '"confidence":0.8,"needs_clarification":false,'
            '"clarification_question":""}'
        )

    def _load_llm_action_json(self, raw_text: str) -> dict[str, Any] | None:
        """
        功能：从模型响应中提取 JSON 对象。
        入参：raw_text（str）：模型原始文本。
        出参：dict[str, Any] | None，成功解析对象返回 dict，否则返回 None。
        异常：JSONDecodeError 内部捕获并降级为 None。
        """
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            loaded = json.loads(raw_text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def _normalize_llm_action(
        self,
        candidate: dict[str, Any] | None,
        raw_input: str,
        actor_id: str | None,
    ) -> dict[str, Any] | None:
        """
        功能：把 LLM 候选动作收敛到主循环动作结构，丢弃不支持类型。
        入参：candidate（dict[str, Any] | None）：模型候选；raw_input（str）：玩家原文；
            actor_id（str | None）：当前角色 ID。
        出参：dict[str, Any] | None，合法候选返回动作结构，否则返回 None。
        异常：不抛异常；缺失字段按 None/空字典降级，交由校验层处理。
        """
        return normalize_action_candidate(candidate, raw_input=raw_input, actor_id=actor_id)

    def _build_self_action(
        self,
        action_type: str,
        raw_input: str,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """
        功能：构造仅作用于当前角色或当前场景的基础动作，统一 actor/target 填充策略。
        入参：action_type（str）：动作类型；raw_input（str）：原始输入；
            actor_id（str | None）：当前角色 ID，角色缺失时允许为 None 供上游校验。
        出参：dict[str, Any]，符合主循环候选动作结构。
        异常：不抛异常；字段按调用方传入值原样写入。
        """
        finalized = self._finalize_candidate(
            {
                "type": action_type,
                "raw_input": raw_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {},
            },
            raw_input,
            actor_id,
        )
        if finalized is None:
            raise RuntimeError(f"基础动作构造失败: action_type={action_type}")
        return finalized

    def _matches_action(
        self,
        normalized_input: str,
        action_keywords: dict[str, Any],
        action_type: str,
    ) -> bool:
        """
        功能：判断输入是否包含某类动作关键词。
        入参：normalized_input（str）：已归一化输入；action_keywords（dict[str, Any]）：关键词配置；
            action_type（str）：待匹配动作类型。
        出参：bool，命中任一字符串关键词返回 True。
        异常：不抛异常；配置不是列表时按空列表处理。
        """
        keywords = action_keywords.get(action_type, [])
        return any(isinstance(keyword, str) and keyword in normalized_input for keyword in keywords)

    def _match_alias_id(self, normalized_input: str, alias_mapping: dict[str, Any]) -> str | None:
        """
        功能：执行 `_match_alias_id` 相关业务逻辑。
        入参：normalized_input；alias_mapping。
        出参：str | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        for resolved_id, aliases in alias_mapping.items():
            if not isinstance(aliases, list):
                continue
            if any(isinstance(alias, str) and alias in normalized_input for alias in aliases):
                return str(resolved_id)
        return None

    def _extract_target_id(self, normalized_input: str) -> str | None:
        """
        功能：执行 `_extract_target_id` 相关业务逻辑。
        入参：normalized_input。
        出参：str | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        target_aliases = self.nlu_rules.get("target_aliases", {})
        return self._match_alias_id(normalized_input, target_aliases)

    def _extract_scene_target_id(
        self,
        normalized_input: str,
        scene_snapshot: Any,
        action_types: set[str],
    ) -> str | None:
        """
        功能：从当前场景的 affordance、NPC 与 Story Pack 交互定义中解析动作目标。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（Any）：当前场景快照；
            action_types（set[str]）：需要匹配的标准动作类型，例如 {"talk"}。
        出参：str | None，命中场景目标返回目标 ID；无匹配或快照非法返回 None。
        异常：不抛异常；脏快照字段按空集合降级，避免 NLU 因展示数据异常中断。
        """
        if not isinstance(scene_snapshot, dict):
            return None

        target_id = self._match_affordance_target(normalized_input, scene_snapshot, action_types)
        if target_id is not None:
            return target_id

        target_id = self._match_visible_npc_target(normalized_input, scene_snapshot)
        if target_id is not None:
            return target_id

        return self._match_interactable_target(normalized_input, scene_snapshot, action_types)

    def _match_affordance_target(
        self,
        normalized_input: str,
        scene_snapshot: dict[str, Any],
        action_types: set[str],
    ) -> str | None:
        """
        功能：从 scene_snapshot.affordances 中按按钮文案反查 target_id。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（dict[str, Any]）：场景快照；
            action_types（set[str]）：允许匹配的动作类型。
        出参：str | None，命中返回 affordance.target_id。
        异常：不抛异常；affordances 非列表或条目非法时跳过。
        """
        raw_affordances = scene_snapshot.get("affordances", [])
        if not isinstance(raw_affordances, list):
            return None
        for item in raw_affordances:
            if not isinstance(item, dict):
                continue
            if str(item.get("action_type") or "") not in action_types:
                continue
            target_id = str(item.get("target_id") or "").strip()
            if not target_id:
                continue
            candidates = [
                str(item.get("label") or ""),
                str(item.get("user_input") or ""),
                str(item.get("object_id") or ""),
                str(item.get("slot_id") or ""),
            ]
            if self._any_candidate_contains(normalized_input, candidates):
                return target_id
        return None

    def _match_visible_npc_target(
        self,
        normalized_input: str,
        scene_snapshot: dict[str, Any],
    ) -> str | None:
        """
        功能：从 visible_npcs 中按实体名、展示名或别名解析 NPC 目标。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（dict[str, Any]）：场景快照。
        出参：str | None，命中返回 NPC entity_id/id。
        异常：不抛异常；visible_npcs 非列表或条目非法时跳过。
        """
        raw_npcs = scene_snapshot.get("visible_npcs", [])
        if not isinstance(raw_npcs, list):
            return None
        for item in raw_npcs:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("entity_id") or item.get("id") or "").strip()
            if not target_id:
                continue
            candidates = [
                target_id,
                str(item.get("label") or ""),
                str(item.get("name") or ""),
            ]
            aliases = item.get("aliases", [])
            if isinstance(aliases, list):
                candidates.extend(str(alias) for alias in aliases if isinstance(alias, str))
            if self._any_candidate_contains(normalized_input, candidates):
                return target_id
        return None

    def _match_interactable_target(
        self,
        normalized_input: str,
        scene_snapshot: dict[str, Any],
        action_types: set[str],
    ) -> str | None:
        """
        功能：从 Story Pack interactables 的 target_ref 解析交互目标。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（dict[str, Any]）：场景快照；
            action_types（set[str]）：允许匹配的交互 kind。
        出参：str | None，命中返回 interactable.target_ref。
        异常：不抛异常；interactables 非列表或条目非法时跳过。
        """
        raw_interactables = scene_snapshot.get("interactables", [])
        if not isinstance(raw_interactables, list):
            return None
        for item in raw_interactables:
            if not isinstance(item, dict):
                continue
            if str(item.get("kind") or "") not in action_types:
                continue
            target_id = str(item.get("target_ref") or "").strip()
            if not target_id:
                continue
            candidates = [
                target_id,
                str(item.get("interaction_id") or ""),
                str(item.get("label") or ""),
            ]
            aliases = item.get("aliases", [])
            if isinstance(aliases, list):
                candidates.extend(str(alias) for alias in aliases if isinstance(alias, str))
            if self._any_candidate_contains(normalized_input, candidates):
                return target_id
        return None

    def _any_candidate_contains(self, normalized_input: str, candidates: list[str]) -> bool:
        """
        功能：判断候选别名是否出现在归一化输入中。
        入参：normalized_input（str）：已归一化输入；candidates（list[str]）：候选文案列表。
        出参：bool，任一非空候选命中返回 True。
        异常：不抛异常；空候选或空输入返回 False。
        """
        if not normalized_input:
            return False
        for candidate in candidates:
            normalized_candidate = candidate.strip().lower()
            if normalized_candidate and normalized_candidate in normalized_input:
                return True
        return False

    def _extract_location_id(
        self,
        normalized_input: str,
        scene_snapshot: Any = None,
    ) -> str:
        """
        功能：从输入和当前场景出口中解析目标地点；模糊继续前进时选择唯一出口。
        入参：normalized_input（str）：已归一化输入；
            scene_snapshot（Any）：主循环场景快照，非 dict 时降级为仅查静态别名。
        出参：str，解析成功返回地点 ID，无法判断返回 unknown。
        异常：不抛异常；快照结构不完整时忽略该来源。
        """
        if isinstance(scene_snapshot, dict):
            exits = scene_snapshot.get("exits", [])
            if isinstance(exits, list):
                for exit_info in exits:
                    if not isinstance(exit_info, dict):
                        continue
                    aliases = exit_info.get("aliases", [])
                    label = str(exit_info.get("label", ""))
                    direction = str(exit_info.get("direction", ""))
                    candidates = [label, direction]
                    if isinstance(aliases, list):
                        candidates.extend(str(alias) for alias in aliases if isinstance(alias, str))
                    if any(candidate and candidate in normalized_input for candidate in candidates):
                        return str(exit_info.get("location_id", "unknown"))
                if len(exits) == 1 and any(
                    keyword in normalized_input
                    for keyword in ["继续", "前进", "赶路", "路上", "移动", "走"]
                ):
                    return str(exits[0].get("location_id", "unknown"))

        location_aliases = self.nlu_rules.get("location_aliases", {})
        return self._match_alias_id(normalized_input, location_aliases) or "unknown"

    def _extract_item_id(self, normalized_input: str) -> str | None:
        """
        功能：执行 `_extract_item_id` 相关业务逻辑。
        入参：normalized_input。
        出参：str | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        item_aliases = self.nlu_rules.get("item_aliases", {})
        return self._match_alias_id(normalized_input, item_aliases)

    def _extract_skill_id(self, normalized_input: str) -> str:
        """
        功能：从规则配置的 skill_aliases 中解析技能 ID，未命中时返回默认专注技能。
        入参：normalized_input（str）：已归一化玩家输入。
        出参：str，解析出的技能 ID；未命中时为 focus。
        异常：不抛异常；配置缺失按 focus 降级，保持技能基础链路可玩。
        """
        skill_aliases = self.nlu_rules.get("skill_aliases", {})
        return self._match_alias_id(normalized_input, skill_aliases) or "focus"

    def _extract_intent(self, normalized_input: str, fallback: str) -> str:
        """
        功能：将扩展自然表达收敛为参数 intent，避免膨胀 ActionType。
        入参：normalized_input（str）：已归一化输入；fallback（str）：默认意图。
        出参：str，供主循环和 A2 交互槽参考的意图标签。
        异常：不抛异常。
        """
        intent_keywords = {
            "search": ["搜索", "搜查", "翻找", "寻找"],
            "take": ["拾取", "捡起", "拿起", "拿走"],
            "open": ["打开", "推开", "拉开"],
            "close": ["关闭", "关上"],
            "equip": ["装备", "穿上", "戴上"],
            "give": ["给予", "交给", "递给"],
        }
        for intent, keywords in intent_keywords.items():
            if any(keyword in normalized_input for keyword in keywords):
                return intent
        return fallback

    def _extract_manner(self, normalized_input: str) -> str:
        """
        功能：提取动作方式，例如潜行、威胁或劝说，作为非裁决参数保存。
        入参：normalized_input（str）：已归一化输入。
        出参：str，未命中返回空字符串。
        异常：不抛异常。
        """
        manner_keywords = {
            "stealth": ["潜行", "悄悄", "偷偷", "躲藏"],
            "threaten": ["威胁", "恐吓"],
            "persuade": ["劝说", "说服"],
        }
        for manner, keywords in manner_keywords.items():
            if any(keyword in normalized_input for keyword in keywords):
                return manner
        return ""

    def _extract_topic(self, normalized_input: str) -> str:
        """
        功能：为社交动作提取最小话题提示，A1 不做语义裁决，仅保留原文线索。
        入参：normalized_input（str）：已归一化输入。
        出参：str，命中询问类表达时返回原输入，否则为空。
        异常：不抛异常。
        """
        has_topic_keyword = any(keyword in normalized_input for keyword in ["问", "询问", "打听"])
        return normalized_input if has_topic_keyword else ""

    def _extract_object_hint(self, normalized_input: str, scene_snapshot: Any = None) -> str:
        """
        功能：从输入或场景对象中提取对象提示，供 Clarifier 和 A2 对象化交互使用。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（Any）：当前场景快照。
        出参：str，未命中返回空字符串。
        异常：不抛异常；场景结构非法时忽略。
        """
        if isinstance(scene_snapshot, dict):
            for key in ["scene_objects", "visible_items", "visible_npcs"]:
                value = scene_snapshot.get(key, [])
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    candidates = [
                        str(item.get("label") or ""),
                        str(item.get("name") or ""),
                        str(item.get("object_id") or ""),
                        str(item.get("item_id") or ""),
                        str(item.get("entity_id") or ""),
                    ]
                    for candidate in candidates:
                        if candidate and candidate.lower() in normalized_input:
                            return candidate
        return ""

    # ---- A2-Plus 场景别名优先匹配：通用场景实体别名检查 ----
    def _any_scene_alias_matches(
        self,
        normalized_input: str,
        scene_snapshot: Any,
        alias_types: list[str],
        kinds: set[str] | None = None,
    ) -> bool:
        """
        功能：遍历 scene_snapshot 中指定实体类型的 label/name/direction/aliases，
             检查 normalized_input 是否包含任一候选词；命中时返回 True。
             kinds 非空时仅匹配 kind/action_type/object_type 落在集合内的实体。
        入参：normalized_input（str）：已归一化输入；scene_snapshot（Any）：场景快照；
             alias_types（list[str]）：实体类型键列表；kinds（set[str] | None）：可选类型过滤。
        出参：bool，命中任一实体别名返回 True。
        异常：不抛异常；快照缺失或结构不完整返回 False。
        """
        if scene_snapshot is None:
            return False

        snap: Any = scene_snapshot if isinstance(scene_snapshot, dict) else scene_snapshot
        for entity_key in alias_types:
            if isinstance(snap, dict):
                entities = snap.get(entity_key, [])
            else:
                entities = getattr(snap, entity_key, None) or []
            if not isinstance(entities, list):
                continue

            for entity in entities:
                if isinstance(entity, dict):
                    label = str(entity.get("label", ""))
                    name = str(entity.get("name", ""))
                    direction = str(entity.get("direction", ""))
                    aliases = entity.get("aliases", [])
                    entity_kind = str(
                        entity.get("kind")
                        or entity.get("action_type")
                        or entity.get("object_type")
                        or ""
                    )
                else:
                    label = str(getattr(entity, "label", ""))
                    name = str(getattr(entity, "name", ""))
                    direction = str(getattr(entity, "direction", ""))
                    aliases = getattr(entity, "aliases", None) or []
                    entity_kind = str(
                        getattr(entity, "kind", "")
                        or getattr(entity, "action_type", "")
                        or getattr(entity, "object_type", "")
                        or ""
                    )
                if kinds is not None and entity_kind not in kinds:
                    continue

                candidates: list[str] = []
                if label:
                    candidates.append(label)
                if name and name != label:
                    candidates.append(name)
                if direction and direction not in candidates:
                    candidates.append(direction)
                if isinstance(aliases, list):
                    candidates.extend(str(a) for a in aliases if isinstance(a, str))

                for candidate in candidates:
                    candidate_norm = str(candidate).strip().lower()
                    if candidate_norm and candidate_norm in normalized_input:
                        return True

        return False
