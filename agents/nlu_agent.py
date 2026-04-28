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
        功能：将玩家输入解析为最小结构化动作；优先使用场景快照解析模糊移动，避免产生 unknown 地点。
        入参：user_input（str）：玩家自然语言，空白输入返回 None；
            context（dict[str, Any] | None）：角色与场景上下文，可包含 id、scene_snapshot。
        出参：dict[str, Any] | None，识别成功返回候选动作 JSON，失败返回 None。
        异常：当前实现不主动抛业务异常；上下文字段缺失时按 None/空快照降级。
        """
        normalized = user_input.strip().lower()
        if not normalized:
            return None

        actor_id = context["id"] if context and "id" in context else None
        scene_snapshot = context.get("scene_snapshot") if context else None

        action_keywords = self.nlu_rules.get("action_keywords", {})

        if self._matches_action(normalized, action_keywords, "attack"):
            return self._finalize_candidate({
                "type": "attack",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_target_id(normalized),
                "parameters": {},
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "talk"):
            return self._finalize_candidate({
                "type": "talk",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": self._extract_target_id(normalized),
                "parameters": {},
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "commit_sandbox"):
            return self._finalize_candidate({
                "type": "commit_sandbox",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {},
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "discard_sandbox"):
            return self._finalize_candidate({
                "type": "discard_sandbox",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {},
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "move"):
            return self._finalize_candidate({
                "type": "move",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": None,
                "parameters": {
                    "location_id": self._extract_location_id(normalized, scene_snapshot),
                },
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "rest"):
            return self._build_self_action("rest", user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "wait"):
            return self._build_self_action("wait", user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "observe"):
            return self._build_self_action("observe", user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "inspect"):
            return self._build_self_action("inspect", user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "use_item"):
            return self._finalize_candidate({
                "type": "use_item",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": actor_id,
                "parameters": {"item_id": self._extract_item_id(normalized)},
            }, user_input, actor_id)

        if self._matches_action(normalized, action_keywords, "interact"):
            return self._finalize_candidate({
                "type": "observe",
                "raw_input": user_input,
                "actor_id": actor_id,
                "target_id": None,
                "parameters": {},
            }, user_input, actor_id)

        if self.llm_enabled:
            return self._parse_with_llm(user_input, actor_id, scene_snapshot)
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
            "动作 type 只能是 observe, wait, rest, move, talk, inspect, use_item, attack。"
            "输出字段必须包含 type, target_id, parameters, confidence, "
            "needs_clarification, clarification_question。"
            "如果是移动，parameters.location_id 必须优先来自 scene_snapshot.exits。"
            "如果目标、方向或对象不明确，设置 needs_clarification=true 并提出中文澄清问题。"
            "只输出 JSON 对象，不要解释。"
            f"\n玩家输入: {user_input}"
            f"\nscene_snapshot: {json.dumps(compact_scene, ensure_ascii=False)}"
            "\nJSON格式: {\"type\":\"observe\",\"target_id\":null,\"parameters\":{},"
            "\"confidence\":0.8,\"needs_clarification\":false,"
            "\"clarification_question\":\"\"}"
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
        finalized = self._finalize_candidate({
            "type": action_type,
            "raw_input": raw_input,
            "actor_id": actor_id,
            "target_id": actor_id,
            "parameters": {},
        }, raw_input, actor_id)
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
                        candidates.extend(
                            str(alias) for alias in aliases if isinstance(alias, str)
                        )
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
