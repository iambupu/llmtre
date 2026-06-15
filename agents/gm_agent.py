"""
游戏主持人智能体逻辑（Game Master）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from difflib import get_close_matches
from typing import Any, cast

from config.agent_model_loader import get_agent_model_binding
from core.event_bus import EventBus
from game_workflows.action_text import build_move_action_text
from game_workflows.main_loop_config import load_main_loop_rules
from state.contracts.agent import GMOutputBlock, QuickActionCandidate

logger = logging.getLogger("Agent.GM")
GM_GENERATED_QUICK_ACTIONS_KEY = "_gm_generated_quick_actions"
GM_GENERATED_QUICK_ACTION_CANDIDATES_KEY = "_gm_generated_quick_action_candidates"
CANONICAL_INTENT_KEY_WHITELIST: set[str] = {
    "inspect_local",
    "observe_local",
    "wait_local",
    "rest_local",
    "move_to_exit",
    "use_inventory_item",
    "talk_to_npc",
    "attack_target",
    "inspect_object",
    "generic_action",
}


class GMAgent:
    """游戏主持人智能体。"""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        rules: dict[str, Any] | None = None,
        model_binding_key: str = "agents.gm",
    ):
        """
        功能：初始化 GM 智能体，并按绑定配置决定是否启用真实 LLM 渲染路径。
        入参：event_bus（EventBus | None）：事件总线实例。
        入参：rules（dict[str, Any] | None）：主循环规则配置；为空时自动加载。
        入参：model_binding_key（str）：Agent 模型绑定键，默认值为 `agents.gm`。
        出参：无显式返回值；实例初始化后会暴露 `model_binding` 快照与 `llm_enabled` 开关。
        异常：规则文件或模型配置解析异常默认向上抛出。
            绑定项缺失时按 deterministic 降级，不中断初始化。
        """
        self.event_bus = event_bus
        loaded_rules = rules if rules is not None else load_main_loop_rules()
        # 配置来源：GM prompt 需要读取默认剧本策略；保存完整规则快照，避免只保留模板导致策略丢失。
        self.rules = loaded_rules
        self.templates = loaded_rules.get("narrative_templates", {})
        self.model_binding_key = model_binding_key
        self.model_binding = get_agent_model_binding(model_binding_key)
        self.llm_enabled = bool(self.model_binding.get("enabled", False))
        self.llm_config = dict(self.model_binding.get("llm_config") or {})
        self.llm_timeout_seconds = int(self.model_binding.get("timeout_seconds", 30))

    def render(
        self,
        state: dict[str, Any],
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        """
        功能：根据当前状态生成叙事反馈。若 LLM 开关开启则优先尝试真实模型，失败后回退模板渲染。
        入参：state（dict[str, Any]）：当前主循环状态快照，至少包含动作、校验结果与角色信息；
            stream_callback（Callable[[str], None] | None，默认 None）：叙事片段回调，
            用于 SSE 逐段输出。
        出参：str，可直接给玩家展示的叙事文本。
        异常：模型调用异常在内部捕获并降级到模板渲染；流式回调异常被忽略，模板渲染异常向上抛出。
        """
        # 请求边界：快捷行动只挂在当前 state，避免共享 GMAgent 实例跨 Web session 串话。
        state.pop(GM_GENERATED_QUICK_ACTIONS_KEY, None)
        state.pop(GM_GENERATED_QUICK_ACTION_CANDIDATES_KEY, None)
        if self.llm_enabled:
            llm_text = self._render_with_llm(state, stream_callback=stream_callback)
            if llm_text:
                return llm_text
        return self._render_with_template(state)

    def render_block(
        self,
        state: dict[str, Any],
        stream_callback: Callable[[str], None] | None = None,
    ) -> GMOutputBlock:
        """
        功能：生成标准 GM 输出块，统一叙事、失败原因、下一步建议和快捷行动。
        入参：state（dict[str, Any]）：当前主循环状态；
            stream_callback（Callable[[str], None] | None，默认 None）：流式片段回调。
        出参：GMOutputBlock，供 A1 TurnResult 直接映射。
        异常：LLM 异常由 render 内部降级；模板异常向上抛出由主循环处理。
        """
        narrative = self.render(state, stream_callback=stream_callback)
        narrative = self._merge_trigger_narrative(
            narrative,
            state.get("trigger_events", []),
        )
        quick_actions = self.suggest_quick_actions(state, narrative)
        quick_action_candidates = self.suggest_quick_action_candidates(
            state,
            narrative,
            quick_actions,
        )
        failure_reason = self._build_failure_reason(state)
        suggested_next_step = quick_actions[0] if quick_actions else self._build_next_step(state)
        return GMOutputBlock(
            narrative=narrative,
            failure_reason=failure_reason,
            suggested_next_step=suggested_next_step,
            quick_actions=quick_actions,
            quick_action_candidates=quick_action_candidates,
        )

    def _merge_trigger_narrative(self, final_response: str, trigger_events: Any) -> str:
        """
        功能：把剧本触发器产生的叙事正文合入 GM 输出，避免线索只停留在结构化事件里。
        入参：final_response（str）：模板或 LLM 生成的基础旁白；
            trigger_events（Any）：主循环写入的触发事件列表，可能包含 narrative_text。
        出参：str，已去重的玩家可见旁白。
        异常：不抛异常；事件列表结构异常时保留基础旁白作为降级结果。
        """
        # 降级路径：触发器叙事不是状态写入来源，只是展示补充；非法事件不能阻断回合返回。
        base = final_response.strip()
        if not isinstance(trigger_events, list):
            return base
        narrative_parts: list[str] = []
        seen = {base} if base else set()
        for event in trigger_events:
            if not isinstance(event, dict):
                continue
            effects = event.get("effects", [])
            if not isinstance(effects, list) or "narrative" not in effects:
                continue
            text = str(event.get("narrative_text") or "").strip()
            if not text or text in seen or (base and text in base):
                continue
            narrative_parts.append(text)
            seen.add(text)
        if not narrative_parts:
            return base
        if not base:
            return " ".join(narrative_parts)
        return f"{base} {' '.join(narrative_parts)}"

    def _render_with_llm(
        self,
        state: dict[str, Any],
        stream_callback: Callable[[str], None] | None = None,
    ) -> str | None:
        """
        功能：调用真实 LLM 生成叙事文本（当前仅支持 ollama）。
        入参：state（dict[str, Any]）：主循环状态快照；
            stream_callback（Callable[[str], None] | None，默认 None）：叙事片段回调。
        出参：str | None，成功时返回模型文本；失败或返回空时返回 None 触发上层降级。
        异常：网络异常、序列化异常、协议异常均内部捕获并记录日志，不向上抛出。
        """
        provider = str(self.llm_config.get("provider", "")).lower()
        if provider != "ollama":
            logger.warning("GM LLM provider=%s 不受支持，回退模板渲染。", provider or "unknown")
            return None

        model = str(self.llm_config.get("model", "")).strip()
        if not model:
            logger.warning("GM LLM 未配置 model，回退模板渲染。")
            return None

        base_url = str(self.llm_config.get("base_url", "http://localhost:11434")).rstrip("/")
        temperature = float(self.llm_config.get("temperature", 0.2))
        max_tokens = self.llm_config.get("max_tokens")
        think_enabled = bool(self.llm_config.get("think", False))
        think_prefix = str(self.llm_config.get("think_prompt_prefix", "/think")).strip()
        prompt = self._build_llm_prompt(state)
        if think_enabled and think_prefix:
            prompt = f"{think_prefix}\n{prompt}"
        options: dict[str, Any] = {"temperature": temperature}
        if isinstance(max_tokens, int):
            options["num_predict"] = max_tokens
        body = {
            "model": model,
            "prompt": prompt,
            "stream": stream_callback is not None,
            "options": options,
        }
        if think_enabled:
            body["think"] = True
        request = urllib.request.Request(
            url=f"{base_url}/api/generate",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.llm_timeout_seconds) as response:
                if stream_callback is not None:
                    text = self._read_ollama_stream(response, stream_callback, state)
                    if text:
                        logger.info(
                            (
                                "GM LLM 流式渲染成功: provider=ollama model=%s "
                                "base_url=%s timeout=%ss think=%s"
                            ),
                            model,
                            base_url,
                            self.llm_timeout_seconds,
                            think_enabled,
                        )
                        return text
                    response_text = ""
                    payload = {}
                else:
                    response_text = response.read().decode("utf-8")
                    payload = json.loads(response_text)
            payload_mapping = self._as_mapping(payload)
            if not payload_mapping:
                self._log_llm_failure(
                    "invalid_payload_type",
                    f"payload_type={type(payload).__name__}",
                    base_url=base_url,
                    model=model,
                    response_preview=str(payload)[:300],
                )
                return None
            text = str(payload_mapping.get("response", "")).strip()
            if text:
                state[GM_GENERATED_QUICK_ACTIONS_KEY] = self._parse_embedded_quick_actions(text)
                logger.info(
                    "GM LLM 渲染成功: provider=ollama model=%s base_url=%s timeout=%ss",
                    model,
                    base_url,
                    self.llm_timeout_seconds,
                )
                return self._clean_visible_response(text).strip()
            self._log_llm_failure(
                "empty_response",
                "ollama response 字段为空",
                base_url=base_url,
                model=model,
                response_preview=response_text[:300],
            )
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

    def _read_ollama_stream(
        self,
        response: Any,
        stream_callback: Callable[[str], None],
        state: dict[str, Any],
    ) -> str:
        """
        功能：读取 Ollama `/api/generate` 的 JSONL 流，边收集最终文本边推送叙事片段。
        入参：response（Any）：urllib HTTP 响应对象；
            stream_callback（Callable[[str], None]）：片段回调；
            state（dict[str, Any]）：当前请求状态，用于保存请求局部快捷行动。
        出参：str，合并后的完整叙事文本。
        异常：单行 JSON 解析失败时跳过该行；回调异常被捕获并记录，避免中断模型读取。
        """
        chunks: list[str] = []
        hidden_tag = ""
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("GM LLM 流式响应行无法解析，已跳过: preview=%r", line[:120])
                continue
            if not isinstance(payload, dict):
                continue
            delta = str(payload.get("response", ""))
            if delta:
                chunks.append(delta)
                visible_delta, hidden_tag = self._filter_hidden_delta(delta, hidden_tag)
                if visible_delta:
                    try:
                        stream_callback(visible_delta)
                    except Exception as callback_error:  # noqa: BLE001
                        logger.warning("GM LLM 流式片段回调失败，已忽略: %s", callback_error)
            if bool(payload.get("done", False)):
                break
        full_text = self._remove_thinking_blocks("".join(chunks))
        state[GM_GENERATED_QUICK_ACTIONS_KEY] = self._parse_embedded_quick_actions(full_text)
        return self._clean_visible_response(full_text).strip()

    def _filter_hidden_delta(self, delta: str, hidden_tag: str) -> tuple[str, str]:
        """
        功能：从增量文本中过滤隐藏块与叙事包装标签，只把可见正文推给前端。
        入参：delta（str）：模型本次返回片段；hidden_tag（str）：当前未闭合隐藏标签。
        出参：tuple[str, str]，可展示片段和新的隐藏标签状态。
        异常：不抛异常；标签不完整时按当前状态保守过滤。
        """
        visible: list[str] = []
        index = 0
        while index < len(delta):
            if hidden_tag:
                end_tag = f"</{hidden_tag}>"
                end = delta.find(end_tag, index)
                if end < 0:
                    return "".join(visible), hidden_tag
                index = end + len(end_tag)
                hidden_tag = ""
                continue
            think_start = delta.find("<think>", index)
            actions_start = delta.find("<quick_actions>", index)
            starts = [
                (think_start, "think"),
                (actions_start, "quick_actions"),
            ]
            starts = [(pos, tag) for pos, tag in starts if pos >= 0]
            if not starts:
                visible.append(self._remove_narrative_tags(delta[index:]))
                break
            start, tag = min(starts, key=lambda item: item[0])
            visible.append(self._remove_narrative_tags(delta[index:start]))
            index = start + len(f"<{tag}>")
            hidden_tag = tag
        return "".join(visible), hidden_tag

    def _clean_visible_response(self, text: str) -> str:
        """
        功能：清理模型协议标签，返回可展示、可落库的叙事正文。
        入参：text（str）：模型完整响应，可能包含 think、quick_actions 与 trpg_narrative 标签。
        出参：str，移除隐藏块和叙事包装标签后的文本。
        异常：不抛异常；未闭合隐藏块按既有降级策略截断尾部，避免协议内容外泄到前端。
        """
        # 事务边界：这里只做展示文本清洗，不解析动作、不写状态，快捷动作仍由专门解析函数处理。
        without_thinking = self._remove_thinking_blocks(text)
        without_actions = self._remove_quick_actions_block(without_thinking)
        return self._remove_narrative_tags(without_actions)

    def _remove_narrative_tags(self, text: str) -> str:
        """
        功能：移除 `<trpg_narrative>` 叙事包装标签，保留标签内部正文。
        入参：text（str）：完整响应或流式可见片段。
        出参：str，不含叙事包装标签的文本。
        异常：不抛异常；标签缺失时原样返回，大小写不匹配时按原样降级。
        """
        return text.replace("<trpg_narrative>", "").replace("</trpg_narrative>", "")

    def _remove_thinking_blocks(self, text: str) -> str:
        """
        功能：清理完整响应中的 `<think>...</think>` 块，保证最终落库文本只包含叙事正文。
        入参：text（str）：模型完整响应。
        出参：str，移除思考块后的文本。
        异常：不抛异常；未闭合思考块会移除从 `<think>` 开始的尾部内容。
        """
        cleaned: list[str] = []
        index = 0
        while index < len(text):
            start = text.find("<think>", index)
            if start < 0:
                cleaned.append(text[index:])
                break
            cleaned.append(text[index:start])
            end = text.find("</think>", start + len("<think>"))
            if end < 0:
                break
            index = end + len("</think>")
        return "".join(cleaned)

    def _parse_embedded_quick_actions(self, text: str) -> list[str]:
        """
        功能：解析 GM 叙事响应中隐藏的 `<quick_actions>` JSON 数组。
        入参：text（str）：模型完整响应。
        出参：list[str]，最多 4 条快捷行动。
        异常：标签缺失或 JSON 非法时返回空列表，交由兜底策略处理。
        """
        start_tag = "<quick_actions>"
        end_tag = "</quick_actions>"
        start = text.find(start_tag)
        end = text.find(end_tag, start + len(start_tag))
        if start < 0 or end < 0:
            return []
        return self._parse_quick_actions(text[start + len(start_tag) : end])

    def _remove_quick_actions_block(self, text: str) -> str:
        """
        功能：移除模型响应中的快捷行动隐藏块，保证玩家叙事区只显示正文。
        入参：text（str）：模型完整响应。
        出参：str，移除 `<quick_actions>...</quick_actions>` 后的文本。
        异常：不抛异常；未闭合标签会移除从开始标签之后的尾部。
        """
        start_tag = "<quick_actions>"
        end_tag = "</quick_actions>"
        start = text.find(start_tag)
        if start < 0:
            return text
        end = text.find(end_tag, start + len(start_tag))
        if end < 0:
            return text[:start]
        return text[:start] + text[end + len(end_tag) :]

    def suggest_quick_actions(self, state: dict[str, Any], final_response: str) -> list[str]:
        """
        功能：基于本回合输出和场景快照生成 4 个可点击快捷行动，优先保留本次 GM 隐藏块的动态建议。
        入参：state（dict[str, Any]）：当前主循环状态；final_response（str）：本回合叙事输出。
        出参：list[str]，最多 4 条可直接作为玩家输入的中文短句。
        异常：LLM 调用、JSON 解析或格式异常均内部降级到场景建议动作；
            隐藏块解析失败时返回空并继续兜底。
        """
        generated_actions: list[str] = []
        embedded_actions = state.get(GM_GENERATED_QUICK_ACTIONS_KEY)
        if isinstance(embedded_actions, list):
            generated_actions = [
                str(action).strip() for action in embedded_actions if str(action).strip()
            ]
        # 请求边界：render 已解析本轮动态按钮时，以隐藏块为准，
        # 避免二次 LLM 或 affordance 覆盖模型给出的上下文短句。
        if generated_actions:
            ranked_affordance = self._rank_affordance_quick_actions(state, final_response)
            return self._merge_actions(generated_actions, ranked_affordance, limit=4)
        if self.llm_enabled:
            llm_actions = self._suggest_quick_actions_with_llm(state, final_response)
            if llm_actions:
                generated_actions = llm_actions
        if generated_actions:
            ranked_affordance = self._rank_affordance_quick_actions(state, final_response)
            return self._merge_actions(generated_actions, ranked_affordance, limit=4)
        ranked_affordance = self._rank_affordance_quick_actions(state, final_response)
        if ranked_affordance:
            return ranked_affordance[:4]
        return []

    def suggest_quick_action_candidates(
        self,
        state: dict[str, Any],
        final_response: str,
        quick_actions: list[str],
    ) -> list[QuickActionCandidate]:
        """
        功能：生成结构化快捷动作候选，优先使用 LLM 结构化输出，失败时回退规则候选。
        入参：state（dict[str, Any]）：当前回合状态；
            final_response（str）：本回合叙事文本；
            quick_actions（list[str]）：已生成的快捷动作文案。
        出参：list[QuickActionCandidate]，用于后端对象约束落桶。
        异常：LLM 调用/解析失败内部降级，不向上抛出，保持回合主链稳定。
        """
        embedded = state.get(GM_GENERATED_QUICK_ACTION_CANDIDATES_KEY)
        if isinstance(embedded, list):
            cleaned_embedded = self._sanitize_quick_action_candidates(embedded)
            if cleaned_embedded:
                return cleaned_embedded
        if self.llm_enabled:
            llm_candidates = self._suggest_quick_action_candidates_with_llm(
                state,
                final_response,
                quick_actions,
            )
            if llm_candidates:
                return llm_candidates
        return self._fallback_quick_action_candidates(state, quick_actions)

    def _affordance_quick_actions(self, state: dict[str, Any]) -> list[str]:
        """
        功能：优先从 scene_snapshot.affordances 提取可点击行动，确保 GM 不生成越界动作。
        入参：state（dict[str, Any]）：当前状态。
        出参：list[str]，最多 4 条可直接提交的行动。
        异常：不抛异常；字段缺失时返回空列表走旧兜底。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        raw_affordances = scene.get("affordances", [])
        if not isinstance(raw_affordances, list):
            return []
        actions: list[str] = []
        seen: set[str] = set()
        for item in raw_affordances:
            if not isinstance(item, dict) or not bool(item.get("enabled", False)):
                continue
            text = str(item.get("user_input") or item.get("label") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            actions.append(text[:40])
            if len(actions) >= 4:
                break
        return actions

    def _rank_affordance_quick_actions(
        self,
        state: dict[str, Any],
        final_response: str,
    ) -> list[str]:
        """
        功能：按本轮动作意图和叙事文本对 affordance 行动打分排序，提升“动作-叙事”相关性。
        入参：state（dict[str, Any]）：当前回合状态；final_response（str）：本轮叙事文本。
        出参：list[str]，按相关性排序后的可点击行动。
        异常：不抛异常；字段缺失时按默认分值排序。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        raw_affordances = scene.get("affordances", [])
        if not isinstance(raw_affordances, list):
            return []
        action = self._as_mapping(state.get("action_intent"))
        intent_type = str(action.get("type") or "").strip().lower()
        user_input = str(state.get("user_input") or "").strip()
        scored: list[tuple[int, int, str]] = []
        for index, item in enumerate(raw_affordances):
            if not isinstance(item, dict) or not bool(item.get("enabled", False)):
                continue
            text = str(item.get("user_input") or item.get("label") or "").strip()
            if not text:
                continue
            score = 0
            action_type = str(item.get("action_type") or "").strip().lower()
            if action_type == intent_type and intent_type:
                score += 45
            if text and text in final_response:
                score += 35
            if user_input and (user_input in text or text in user_input):
                score += 25
            priority = item.get("priority")
            if isinstance(priority, int):
                score += max(0, 20 - priority)
            scored.append((score, -index, text[:40]))
        scored.sort(reverse=True)
        result: list[str] = []
        seen: set[str] = set()
        for _, _, text in scored:
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _normalize_generated_actions(
        self,
        generated_actions: list[str],
        affordance_actions: list[str],
    ) -> list[str]:
        """
        功能：将 LLM 生成动作映射到可执行 affordance 输入，无法映射的候选一律丢弃。
        入参：generated_actions（list[str]）：模型生成动作；
            affordance_actions（list[str]）：当前场景可执行动作。
        出参：list[str]，去重后的可执行动作列表。
        异常：不抛异常；无法映射时丢弃候选，避免 GM 越权生成按钮。
        """
        if not generated_actions or not affordance_actions:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in generated_actions:
            candidate = raw.strip()[:40]
            if not candidate:
                continue
            mapped = candidate if candidate in affordance_actions else ""
            if not mapped:
                close = get_close_matches(candidate, affordance_actions, n=1, cutoff=0.6)
                mapped = close[0] if close else ""
            if not mapped:
                continue
            if mapped in seen:
                continue
            seen.add(mapped)
            normalized.append(mapped)
        return normalized

    def _merge_actions(
        self,
        primary: list[str],
        secondary: list[str],
        limit: int = 4,
    ) -> list[str]:
        """
        功能：合并两组候选动作并去重，保证优先级顺序稳定。
        入参：primary（list[str]）：高优先级动作；secondary（list[str]）：补位动作；
            limit（int，默认 4）：最大返回数量。
        出参：list[str]，合并去重后的结果。
        异常：不抛异常；空列表输入时返回空结果。
        """
        merged: list[str] = []
        seen: set[str] = set()
        for action in [*primary, *secondary]:
            text = str(action).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
            if len(merged) >= limit:
                break
        return merged

    def _build_failure_reason(self, state: dict[str, Any]) -> str:
        """
        功能：从验证错误和澄清状态提取标准失败原因。
        入参：state（dict[str, Any]）：当前回合状态。
        出参：str，合法动作返回空字符串。
        异常：不抛异常，字段缺失时按空字符串降级。
        """
        if state.get("is_valid", False):
            return ""
        existing = str(state.get("failure_reason") or "").strip()
        if existing:
            return existing
        errors = state.get("validation_errors", [])
        if isinstance(errors, list) and errors:
            return "；".join(str(item) for item in errors)
        question = str(state.get("clarification_question") or "").strip()
        return "行动信息还不够明确。" if question else "行动未能成立。"

    def _build_next_step(self, state: dict[str, Any]) -> str:
        """
        功能：生成降级下一步建议，优先使用 affordance。
        入参：state（dict[str, Any]）：当前回合状态。
        出参：str。
        异常：不抛异常。
        """
        actions = self._affordance_quick_actions(state)
        if actions:
            return actions[0]
        question = str(state.get("clarification_question") or "").strip()
        return question or "观察周围"

    def _suggest_quick_actions_with_llm(
        self,
        state: dict[str, Any],
        final_response: str,
    ) -> list[str]:
        """
        功能：调用 Ollama 为前端生成动态快捷行动，要求每次结合当前叙事给出不同选择。
        入参：state（dict[str, Any]）：当前状态；final_response（str）：本回合最终叙事。
        出参：list[str]，解析成功返回 1..4 条行动，失败返回空列表。
        异常：网络、协议、JSON 解析异常均内部捕获并记录日志，返回空列表降级。
        """
        provider = str(self.llm_config.get("provider", "")).lower()
        if provider != "ollama":
            return []
        model = str(self.llm_config.get("model", "")).strip()
        if not model:
            return []

        base_url = str(self.llm_config.get("base_url", "http://localhost:11434")).rstrip("/")
        temperature = max(float(self.llm_config.get("temperature", 0.2)), 0.75)
        prompt = self._build_quick_actions_prompt(state, final_response)
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 256},
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
            payload_mapping = self._as_mapping(payload)
            raw_text = str(payload_mapping.get("response", "")).strip()
            actions = self._parse_quick_actions(raw_text)
            if actions:
                logger.info("GM 快捷行动生成成功: count=%s model=%s", len(actions), model)
            return actions
        except Exception as error:  # noqa: BLE001
            logger.warning("GM 快捷行动生成失败，已降级为场景建议: %s", error)
            return []

    def _suggest_quick_action_candidates_with_llm(
        self,
        state: dict[str, Any],
        final_response: str,
        quick_actions: list[str],
    ) -> list[QuickActionCandidate]:
        """
        功能：调用 LLM 生成结构化快捷动作候选对象，仅产出意图键/对象提示/展示文本。
        入参：state（dict[str, Any]）：当前回合状态；
            final_response（str）：本回合叙事；
            quick_actions（list[str]）：已生成的快捷动作文案（用于上下文）。
        出参：list[QuickActionCandidate]，解析成功返回候选，失败返回空列表。
        异常：网络、协议、JSON 解析异常均内部捕获并降级为空列表。
        """
        provider = str(self.llm_config.get("provider", "")).lower()
        if provider != "ollama":
            return []
        model = str(self.llm_config.get("model", "")).strip()
        if not model:
            return []
        base_url = str(self.llm_config.get("base_url", "http://localhost:11434")).rstrip("/")
        prompt = self._build_quick_action_candidates_prompt(state, final_response, quick_actions)
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 384},
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
            payload_mapping = self._as_mapping(payload)
            raw_text = str(payload_mapping.get("response", "")).strip()
            raw_candidates = self._parse_quick_action_candidates(raw_text)
            candidates = self._sanitize_quick_action_candidates(raw_candidates)
            if candidates:
                logger.info("GM 结构化快捷候选生成成功: count=%s model=%s", len(candidates), model)
            return candidates
        except Exception as error:  # noqa: BLE001
            logger.warning("GM 结构化快捷候选生成失败，已降级规则映射: %s", error)
            return []

    def _build_quick_actions_prompt(self, state: dict[str, Any], final_response: str) -> str:
        """
        功能：构建快捷行动生成提示词，限制模型只输出 JSON 数组。
        入参：state（dict[str, Any]）：当前回合状态；final_response（str）：本回合叙事。
        出参：str，发送给 Ollama 的提示词。
        异常：JSON 序列化失败时向上抛出，由调用方捕获降级。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        compact_context = {
            "turn_id": state.get("turn_id"),
            "user_input": state.get("user_input", ""),
            "final_response": final_response,
            "recent_memory": scene.get("recent_memory", ""),
            "current_location": scene.get("current_location"),
            "exits": scene.get("exits"),
            "visible_npcs": scene.get("visible_npcs"),
            "visible_items": scene.get("visible_items"),
            "available_actions": scene.get("available_actions"),
            "suggested_actions": scene.get("suggested_actions"),
        }
        return (
            "根据本回合叙事和场景信息，生成 4 个下一步快捷行动。"
            "每个行动必须是玩家可直接输入的一句中文短命令，8到18个字，"
            "要具体、可执行、彼此不同。不要解释，不要编号，只输出 JSON 字符串数组。"
            f"\n上下文JSON:\n{json.dumps(compact_context, ensure_ascii=False)}"
            '\n输出示例: ["观察木牌","询问老人线索","沿小路前进","检查背包"]'
        )

    def _build_quick_action_candidates_prompt(
        self,
        state: dict[str, Any],
        final_response: str,
        quick_actions: list[str],
    ) -> str:
        """
        功能：构建结构化快捷候选提示词，强制模型仅输出 JSON 对象数组。
        入参：state（dict[str, Any]）：当前回合状态；final_response（str）：叙事文本；
            quick_actions（list[str]）：已生成动作文案。
        出参：str，可直接发送给模型的提示词。
        异常：JSON 序列化失败时向上抛出，由调用方捕获并降级。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        compact_context = {
            "turn_id": state.get("turn_id"),
            "user_input": state.get("user_input", ""),
            "final_response": final_response,
            "quick_actions": quick_actions,
            "current_location": scene.get("current_location"),
            "scene_objects": scene.get("scene_objects"),
            "affordances": scene.get("affordances"),
            "interaction_slots": scene.get("interaction_slots"),
        }
        canonical_keys = sorted(CANONICAL_INTENT_KEY_WHITELIST)
        return (
            "你是 TRPG 动作分类器。只输出 JSON 数组，不要任何解释。"
            "每个元素必须包含 canonical_intent_key、target_object_hint、display_text，"
            "可选 confidence、reason。"
            "canonical_intent_key 只能从白名单中选择，禁止创造新键。"
            f"白名单: {json.dumps(canonical_keys, ensure_ascii=False)}。"
            "target_object_hint 优先使用 object_id，"
            "如 location:scene_id / exit:target_scene / inventory:item_id。"
            "如果无法判断目标对象，target_object_hint 置空字符串。"
            "最多输出 8 条。"
            f"\n上下文JSON:\n{json.dumps(compact_context, ensure_ascii=False)}"
            "\n输出示例: "
            '[{"canonical_intent_key":"move_to_exit",'
            '"target_object_hint":"exit:camp","display_text":"前往营地","confidence":0.9}]'
        )

    def _parse_quick_action_candidates(self, text: str) -> list[dict[str, Any]]:
        """
        功能：解析模型返回的结构化快捷候选 JSON 数组。
        入参：text（str）：模型响应文本，期望为 JSON 数组。
        出参：list[dict[str, Any]]，可解析时返回数组项，失败返回空列表。
        异常：JSON 解析失败内部捕获并降级为空列表。
        """
        if not text:
            return []
        stripped = text.strip()
        if "```" in stripped:
            stripped = stripped.replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _sanitize_quick_action_candidates(
        self,
        raw_candidates: list[dict[str, Any]],
    ) -> list[QuickActionCandidate]:
        """
        功能：校验并清洗结构化候选，丢弃非法字段并限制数量，保证后续落桶可控。
        入参：raw_candidates（list[dict[str, Any]]）：模型或嵌入块返回的原始对象列表。
        出参：list[QuickActionCandidate]，最多 8 条合法候选。
        异常：对象构造失败内部捕获并跳过单条，不影响其余候选。
        """
        cleaned: list[QuickActionCandidate] = []
        seen_display: set[str] = set()
        for item in raw_candidates:
            canonical_key = str(item.get("canonical_intent_key") or "").strip()
            target_hint = str(item.get("target_object_hint") or "").strip()
            display_text = str(item.get("display_text") or "").strip()
            if not canonical_key or not display_text:
                continue
            if canonical_key not in CANONICAL_INTENT_KEY_WHITELIST:
                canonical_key = "generic_action"
            if display_text in seen_display:
                continue
            seen_display.add(display_text)
            confidence_raw = item.get("confidence")
            confidence = float(confidence_raw) if isinstance(confidence_raw, (float, int)) else None
            reason = str(item.get("reason") or "")
            cleaned.append(
                QuickActionCandidate(
                    canonical_intent_key=canonical_key,
                    target_object_hint=target_hint,
                    display_text=display_text[:40],
                    confidence=confidence,
                    reason=reason[:120],
                )
            )
            if len(cleaned) >= 8:
                break
        return cleaned

    def _fallback_quick_action_candidates(
        self,
        state: dict[str, Any],
        quick_actions: list[str],
    ) -> list[QuickActionCandidate]:
        """
        功能：在 LLM 结构化输出失败时，根据 affordance 与动作文本构建规则候选。
        入参：state（dict[str, Any]）：当前回合状态；quick_actions（list[str]）：动作文案列表。
        出参：list[QuickActionCandidate]，用于后端后续约束落桶。
        异常：不抛异常；缺失字段时返回尽可能少但合法的候选。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        affordances = scene.get("affordances", [])
        affordance_text_to_object: dict[str, str] = {}
        if isinstance(affordances, list):
            for affordance in affordances:
                if not isinstance(affordance, dict):
                    continue
                text = str(affordance.get("user_input") or affordance.get("label") or "").strip()
                object_id = str(affordance.get("object_id") or "").strip()
                if text and object_id:
                    affordance_text_to_object[text] = object_id
        candidates: list[QuickActionCandidate] = []
        for action in quick_actions:
            text = str(action).strip()
            if not text:
                continue
            object_hint = affordance_text_to_object.get(text, "")
            canonical_key = self._infer_canonical_intent_key(text, object_hint)
            candidates.append(
                QuickActionCandidate(
                    canonical_intent_key=canonical_key,
                    target_object_hint=object_hint,
                    display_text=text,
                )
            )
            if len(candidates) >= 8:
                break
        return candidates

    def _infer_canonical_intent_key(self, action_text: str, object_hint: str) -> str:
        """
        功能：按动作文本与对象提示推断 canonical_intent_key，作为结构化失败兜底。
        入参：action_text（str）：动作文案；object_hint（str）：对象提示。
        出参：str，白名单内 canonical_intent_key。
        异常：不抛异常；无法命中时返回 generic_action。
        """
        text = action_text.strip()
        if object_hint.startswith("exit:") or "前往" in text or "移动" in text:
            return "move_to_exit"
        if object_hint.startswith("inventory:") or "使用" in text or "背包" in text:
            return "use_inventory_item"
        if "检查" in text:
            return "inspect_local"
        if "观察" in text or "查看" in text:
            return "observe_local"
        if "等待" in text:
            return "wait_local"
        if "休息" in text:
            return "rest_local"
        if "交谈" in text or "对话" in text:
            return "talk_to_npc"
        if "攻击" in text:
            return "attack_target"
        return "generic_action"

    def _parse_quick_actions(self, raw_text: str) -> list[str]:
        """
        功能：从模型响应中解析快捷行动 JSON 数组，并做去重和长度限制。
        入参：raw_text（str）：模型原始响应。
        出参：list[str]，最多 4 条非空行动。
        异常：JSON 解析失败时内部返回空列表。
        """
        start = raw_text.find("[")
        end = raw_text.rfind("]")
        if start < 0 or end < start:
            return []
        try:
            loaded = json.loads(raw_text[start : end + 1])
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        actions: list[str] = []
        seen: set[str] = set()
        for item in loaded:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            actions.append(text[:40])
            if len(actions) >= 4:
                break
        return actions

    def _fallback_quick_actions(self, state: dict[str, Any]) -> list[str]:
        """
        功能：在 LLM 不可用时基于场景建议动作生成快捷行动兜底。
        入参：state（dict[str, Any]）：当前状态，优先读取 scene_snapshot.suggested_actions。
        出参：list[str]，固定返回 4 条快捷行动。
        异常：不抛异常；字段缺失时使用通用行动。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        raw_suggestions = scene.get("suggested_actions", [])
        actions = [str(item) for item in raw_suggestions if isinstance(item, str) and item]
        actions.extend(["观察周围", "继续前进", "和附近的人交谈", "检查背包"])
        result: list[str] = []
        seen: set[str] = set()
        for action in actions:
            if action in seen:
                continue
            seen.add(action)
            result.append(action)
            if len(result) >= 4:
                break
        return result

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
        功能：记录 GM LLM 调用失败的可诊断上下文，便于排查连接、模型和协议问题。
        入参：reason（str）：失败分类；detail（str）：异常细节；
            base_url（str）：请求目标服务；model（str）：模型名；
            response_preview（str，默认空）：响应片段，最多记录 300 字符。
        出参：None。
        异常：日志写入失败由 logging 内部处理；本函数不主动抛异常。
        """
        logger.warning(
            (
                "GM LLM 调用失败，回退模板渲染: reason=%s detail=%s "
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
        异常：内部捕获响应体读取异常，避免日志增强逻辑影响模板降级。
        """
        try:
            return error.read(300).decode("utf-8", errors="replace")
        except Exception as read_error:  # noqa: BLE001
            return f"<failed to read error body: {read_error}>"

    def _build_llm_prompt(self, state: dict[str, Any]) -> str:
        """
        功能：构建 GM 叙事模型提示词，约束其只做文案表达不做规则裁决。
        入参：state（dict[str, Any]）：当前状态快照。
        出参：str，发送给 LLM 的完整提示词。
        异常：字段缺失时按空值降级，不抛异常。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        compact_state = {
            "turn_id": state.get("turn_id"),
            "user_input": state.get("user_input", ""),
            "is_valid": state.get("is_valid", False),
            "turn_outcome": state.get("turn_outcome", ""),
            "clarification_question": state.get("clarification_question", ""),
            "validation_errors": state.get("validation_errors", []),
            "action_intent": state.get("action_intent"),
            "physics_diff": state.get("physics_diff"),
            "active_character": state.get("active_character"),
            "scene_snapshot": scene,
            "recent_memory": scene.get("recent_memory", ""),
            "rag_context": state.get("rag_context", ""),
            "default_story_policy": self.rules.get("default_story_policy", {}),
        }
        state_json = json.dumps(compact_state, ensure_ascii=False)
        return (
            "你是 TRPG 的旁白 GM，只负责叙事表达，不允许更改规则结果。"
            "请基于给定状态生成 2-4 句中文叙事，必须引用当前场景、"
            "玩家行动对象或可见线索中的至少一项。"
            "即使玩家输入文本重复，也必须结合 turn_id、recent_memory、场景快照和结算结果，"
            "体现这是新的回合，而不是复用旧响应。"
            "如果 physics_diff、quest_updates 或 trigger_events 没有提供某个状态变化，"
            "禁止编造获得物品、任务完成、位置变化或伤害。"
            "如果 is_valid=false，请礼貌说明失败原因。"
            "叙事正文之后必须追加隐藏块 <quick_actions>，其中放一个 JSON 字符串数组，"
            "包含 4 个下一步玩家可直接输入的中文短行动。"
            "这 4 个行动必须与本轮叙事中出现的实体/线索直接相关，"
            "禁止只输出泛化动作（如固定的观察/等待/前进组合）。"
            "隐藏块格式必须严格为 "
            '<quick_actions>["行动1","行动2","行动3","行动4"]</quick_actions>。'
            f"\n状态JSON:\n{state_json}"
        )

    def _render_with_template(self, state: dict[str, Any]) -> str:
        """
        功能：执行确定性模板渲染路径，作为默认与降级策略。
        入参：state（dict[str, Any]）：当前主循环状态快照。
        出参：str，模板化叙事文本。
        异常：模板格式化异常默认向上抛出。
        """
        action = self._as_mapping(state.get("action_intent"))
        character = self._as_mapping(state.get("active_character"))
        actor_name = str(character.get("name", "旅者"))

        if state.get("turn_outcome") == "clarification":
            return str(state.get("clarification_question") or "你能再具体说明一下吗？")
        if not state.get("is_valid", False):
            raw_errors = state.get("validation_errors", ["行动未能成立。"])
            errors = (
                [str(error) for error in raw_errors]
                if isinstance(raw_errors, list)
                else ["行动未能成立。"]
            )
            template = str(self.templates.get("invalid", "{actor_name}的行动未能成立：{errors}"))
            return template.format(actor_name=actor_name, errors="；".join(errors))
        if not action:
            template = str(self.templates.get("idle", "{actor_name}暂时没有采取有效行动。"))
            return template.format(actor_name=actor_name)

        action_type = str(action.get("type", "unknown"))
        physics_diff = self._as_mapping(state.get("physics_diff"))
        renderer = self._get_template_renderer(action_type)
        if renderer is None:
            template = str(self.templates.get("default", "{actor_name}完成了 {action_type} 行动。"))
            return template.format(actor_name=actor_name, action_type=action_type)
        return renderer(actor_name, action, physics_diff, state)

    def _get_template_renderer(
        self,
        action_type: str,
    ) -> Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any]], str] | None:
        """
        功能：按动作类型返回对应模板渲染函数，避免主函数长分支。
        入参：action_type（str）：动作类型。
        出参：Callable 或 None；未命中返回 None 走默认模板。
        异常：不抛异常；纯查表逻辑。
        """
        renderers: dict[
            str, Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any]], str]
        ] = {
            "attack": self._render_attack_template,
            "talk": self._render_talk_template,
            "move": self._render_move_template,
            "observe": self._render_observe_template,
            "wait": self._render_wait_template,
            "rest": self._render_rest_template,
            "inspect": self._render_inspect_template,
            "use_item": self._render_use_item_template,
            "skill": self._render_skill_template,
            "interact": self._render_interact_template,
            "commit_sandbox": self._render_commit_sandbox_template,
            "discard_sandbox": self._render_discard_sandbox_template,
        }
        return renderers.get(action_type)

    def _render_attack_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染攻击动作模板，命中与未命中分支复用同一函数。
        入参：actor_name/action/physics_diff/state（state 为统一签名预留）。
        出参：str，攻击动作叙事文本。
        异常：模板格式化异常向上抛出。
        """
        del state
        target_id = str(action.get("target_id", "未知目标"))
        attack_roll = self._to_int(physics_diff.get("attack_roll", 0))
        attack_dc = self._to_int(physics_diff.get("attack_dc", 0))
        if not bool(physics_diff.get("attack_hit", False)):
            template = str(
                self.templates.get(
                    "attack_miss",
                    "{actor_name}发起了攻击，但未能命中 {target_id}。"
                    "判定 {attack_roll} 未达到 {attack_dc}。",
                )
            )
            return template.format(
                actor_name=actor_name,
                target_id=target_id,
                attack_roll=attack_roll,
                attack_dc=attack_dc,
            )
        damage = abs(self._to_int(physics_diff.get("target_hp_delta", 0)))
        template = str(
            self.templates.get(
                "attack_hit",
                "{actor_name}发起了攻击，判定 {attack_roll} 超过 {attack_dc}，"
                "对 {target_id} 造成了 {damage} 点伤害。",
            )
        )
        return template.format(
            actor_name=actor_name,
            target_id=target_id,
            attack_roll=attack_roll,
            attack_dc=attack_dc,
            damage=damage,
        )

    def _render_talk_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染交谈动作模板，并补充当前场景里可观察到的回应线索。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的交谈叙事。
        异常：模板格式化异常向上抛出；目标缺失时降级为“附近的存在”。
        """
        target_id = str(action.get("target_id") or "附近的存在")
        target_label = self._resolve_target_label(action, state) or target_id
        mp_cost = abs(min(0, self._to_int(physics_diff.get("mp_delta", 0))))
        template = str(
            self.templates.get(
                "talk",
                "{actor_name}与 {target_id} 进行交谈。",
            )
        )
        base = template.format(
            actor_name=actor_name,
            target_id=target_label,
            mp_cost=mp_cost,
        )
        focus = self._visible_focus_sentence(state)
        return f"{base}{focus}"

    def _render_move_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染移动动作模板，并把目标场景的玩家可读名称写入叙事。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的移动叙事。
        异常：模板格式化异常向上抛出；目标缺失时降级为“未知地点”。
        """
        parameters = self._as_mapping(action.get("parameters"))
        location_id = str(parameters.get("location_id", "未知地点"))
        target_label = self._resolve_location_label(location_id, state) or location_id
        move_text = self._resolve_move_action_text(location_id, state) or build_move_action_text(
            target_label
        )
        mp_cost = abs(min(0, self._to_int(physics_diff.get("mp_delta", 0))))
        template = str(
            self.templates.get(
                "move",
                "{actor_name}{move_text}。",
            )
        )
        if "{move_text}" not in template and "前往了" in template:
            # 降级路径：兼容旧配置里的“前往了 {location_id}”模板，
            # 避免出口 label 已含动词时重复套壳。
            template = "{actor_name}{move_text}。"
        base = template.format(
            actor_name=actor_name,
            location_id=target_label,
            move_text=move_text,
            mp_cost=mp_cost,
        )
        effect = self._effect_sentence(physics_diff)
        return f"{base}{effect}"

    def _resolve_move_action_text(self, location_id: str, state: dict[str, Any]) -> str:
        """
        功能：从当前场景出口解析玩家可读的完整移动动作文本。
        入参：location_id（str）：动作参数中的目标位置、出口标签或 scene_id；
            state（dict[str, Any]）：当前回合状态，优先读取 scene_snapshot.exits。
        出参：str，命中出口时返回规整后的移动短句；未命中返回空字符串。
        异常：不抛异常；scene_snapshot 或 exits 结构异常时按未命中降级。
        """
        if not location_id:
            return ""
        scene = self._as_mapping(state.get("scene_snapshot"))
        exits = scene.get("exits")
        if not isinstance(exits, list):
            return ""
        for exit_item in exits:
            if not isinstance(exit_item, dict):
                continue
            source_ref = self._as_mapping(exit_item.get("source_ref"))
            identifiers = {
                str(exit_item.get("id") or ""),
                str(exit_item.get("to_location_id") or ""),
                str(exit_item.get("target_scene_id") or ""),
                str(exit_item.get("location_id") or ""),
                str(exit_item.get("label") or ""),
                str(exit_item.get("name") or ""),
                str(source_ref.get("target_scene_id") or ""),
                str(source_ref.get("location_id") or ""),
            }
            if location_id not in identifiers:
                continue
            raw_label = str(
                exit_item.get("label")
                or exit_item.get("name")
                or exit_item.get("display_name")
                or location_id
            )
            return build_move_action_text(raw_label)
        return ""

    def _render_observe_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染观察动作模板，优先呈现场景摘要与可见对象线索。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的观察叙事。
        异常：模板格式化异常向上抛出；场景缺失时降级为通用观察文案。
        """
        del action, physics_diff
        scene = self._as_mapping(state.get("scene_snapshot"))
        current_location = self._as_mapping(scene.get("current_location"))
        scene_description = str(
            current_location.get("description")
            or current_location.get("name")
            or "周围暂时没有新的细节。"
        )
        template = str(self.templates.get("observe", "{actor_name}观察周围：{scene_description}"))
        base = template.format(
            actor_name=actor_name,
            scene_description=scene_description,
        )
        focus = self._visible_focus_sentence(state)
        return f"{base}{focus}"

    def _render_wait_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """功能：渲染等待动作模板。入参：actor_name/action/physics_diff/state。出参：str。异常：模板格式化异常向上抛出。"""
        del action, physics_diff, state
        template = str(self.templates.get("wait", "{actor_name}停下来片刻，留意周围的动静。"))
        return template.format(actor_name=actor_name)

    def _render_rest_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """功能：渲染休息动作模板。入参：actor_name/action/physics_diff/state。出参：str。异常：模板格式化异常向上抛出。"""
        del action, state
        hp_delta = self._to_int(physics_diff.get("hp_delta", 0))
        mp_delta = self._to_int(physics_diff.get("mp_delta", 0))
        template = str(
            self.templates.get(
                "rest",
                "{actor_name}短暂休息，恢复了 {hp_delta} 点生命与 {mp_delta} 点法力。",
            )
        )
        return template.format(
            actor_name=actor_name,
            hp_delta=hp_delta,
            mp_delta=mp_delta,
        )

    def _render_inspect_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染检查动作模板，尽量引用具体目标、场景摘要和确定性状态差异。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的检查叙事。
        异常：模板格式化异常向上抛出；目标缺失时降级为当前场景。
        """
        target_label = self._resolve_target_label(action, state) or "当前场景"
        template = str(
            self.templates.get(
                "inspect",
                "{actor_name}仔细检查{target_label}，确认了可走的方向与可互动目标。",
            )
        )
        base = template.format(actor_name=actor_name, target_label=target_label)
        focus = self._visible_focus_sentence(state)
        effect = self._effect_sentence(physics_diff)
        return f"{base}{focus}{effect}"

    def _render_use_item_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """功能：渲染使用物品动作模板。入参：actor_name/action/physics_diff/state。出参：str。异常：模板格式化异常向上抛出。"""
        del state
        parameters = self._as_mapping(action.get("parameters"))
        item_id = str(parameters.get("item_id", "未知物品"))
        hp_delta = self._to_int(physics_diff.get("hp_delta", 0))
        template = str(
            self.templates.get(
                "use_item",
                "{actor_name}使用了 {item_id}，恢复了 {hp_delta} 点生命。",
            )
        )
        return template.format(
            actor_name=actor_name,
            item_id=item_id,
            hp_delta=hp_delta,
        )

    def _render_skill_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染技能动作模板，并把确定性 MP 消耗和状态标记反馈给玩家。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的技能反馈。
        异常：模板格式化异常向上抛出；技能 ID 缺失时降级为 focus。
        """
        del state
        parameters = self._as_mapping(action.get("parameters"))
        skill_id = str(parameters.get("skill_id") or "focus")
        mp_delta = self._to_int(physics_diff.get("mp_delta", 0))
        mp_cost = abs(min(0, mp_delta))
        template = str(
            self.templates.get(
                "skill",
                "{actor_name}发动了 {skill_id}，消耗了 {mp_cost} 点法力。",
            )
        )
        base = template.format(
            actor_name=actor_name,
            skill_id=skill_id,
            mp_cost=mp_cost,
            mp_delta=mp_delta,
        )
        return f"{base}{self._effect_sentence(physics_diff)}"

    def _render_interact_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """
        功能：渲染通用交互动作模板，并引用可见对象作为反馈线索。
        入参：actor_name/action/physics_diff/state，均来自主循环确定性状态快照。
        出参：str，玩家可读的交互叙事。
        异常：模板格式化异常向上抛出；场景缺失时仅返回基础模板。
        """
        del action
        mp_cost = abs(min(0, self._to_int(physics_diff.get("mp_delta", 0))))
        template = str(
            self.templates.get(
                "interact",
                "{actor_name}仔细观察了周围环境。",
            )
        )
        base = template.format(
            actor_name=actor_name,
            mp_cost=mp_cost,
        )
        focus = self._visible_focus_sentence(state)
        return f"{base}{focus}"

    def _render_commit_sandbox_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """功能：渲染并入沙盒动作模板。入参：actor_name/action/physics_diff/state。出参：str。异常：模板格式化异常向上抛出。"""
        del action, physics_diff, state
        template = str(
            self.templates.get(
                "commit_sandbox",
                "{actor_name}将沙盒剧情并入了主线，当前世界状态已更新。",
            )
        )
        return template.format(actor_name=actor_name)

    def _render_discard_sandbox_template(
        self,
        actor_name: str,
        action: dict[str, Any],
        physics_diff: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """功能：渲染放弃沙盒动作模板。入参：actor_name/action/physics_diff/state。出参：str。异常：模板格式化异常向上抛出。"""
        del action, physics_diff, state
        template = str(
            self.templates.get(
                "discard_sandbox",
                "{actor_name}放弃了沙盒剧情，世界状态已回滚到主线。",
            )
        )
        return template.format(actor_name=actor_name)

    def _resolve_target_label(self, action: dict[str, Any], state: dict[str, Any]) -> str:
        """
        功能：从动作目标与场景快照中解析玩家可读目标名称。
        入参：action（dict[str, Any]）：主循环解析出的动作意图；
            state（dict[str, Any]）：当前回合状态快照。
        出参：str，命中场景对象、NPC、物品或位置时返回展示名，否则返回空字符串。
        异常：不抛异常；字段缺失或类型不匹配时保守返回空字符串。
        """
        parameters = self._as_mapping(action.get("parameters"))
        target_id = str(
            action.get("target_id")
            or parameters.get("target_id")
            or parameters.get("object_id")
            or parameters.get("item_id")
            or parameters.get("location_id")
            or ""
        ).strip()
        if not target_id:
            return ""
        scene = self._as_mapping(state.get("scene_snapshot"))
        candidates: list[dict[str, Any]] = []
        for key in ("scene_objects", "visible_npcs", "visible_items", "exits"):
            value = scene.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        current_location = self._as_mapping(scene.get("current_location"))
        if current_location:
            candidates.append(current_location)
        for item in candidates:
            source_ref = self._as_mapping(item.get("source_ref"))
            identifiers = {
                str(item.get("object_id") or ""),
                str(item.get("entity_id") or ""),
                str(item.get("item_id") or ""),
                str(item.get("target_ref") or ""),
                str(item.get("interaction_id") or ""),
                str(item.get("id") or ""),
                str(item.get("to_location_id") or ""),
                str(item.get("target_scene_id") or ""),
                str(source_ref.get("target_ref") or ""),
                str(source_ref.get("interaction_id") or ""),
            }
            if target_id in identifiers:
                return self._display_label(item)
        return target_id

    def _resolve_location_label(self, location_id: str, state: dict[str, Any]) -> str:
        """
        功能：从场景出口或当前位置中解析位置展示名。
        入参：location_id（str）：动作参数中的目标位置；state（dict[str, Any]）：当前回合状态。
        出参：str，命中时返回出口或位置展示名，未命中返回空字符串。
        异常：不抛异常；字段缺失时保守返回空字符串。
        """
        if not location_id:
            return ""
        scene = self._as_mapping(state.get("scene_snapshot"))
        exits = scene.get("exits")
        if isinstance(exits, list):
            for exit_item in exits:
                if not isinstance(exit_item, dict):
                    continue
                source_ref = self._as_mapping(exit_item.get("source_ref"))
                identifiers = {
                    str(exit_item.get("id") or ""),
                    str(exit_item.get("to_location_id") or ""),
                    str(exit_item.get("target_scene_id") or ""),
                    str(exit_item.get("location_id") or ""),
                    str(exit_item.get("label") or ""),
                    str(exit_item.get("name") or ""),
                    str(source_ref.get("target_scene_id") or ""),
                    str(source_ref.get("location_id") or ""),
                }
                if location_id in identifiers:
                    return self._display_label(exit_item)
        current_location = self._as_mapping(scene.get("current_location"))
        if location_id in {
            str(current_location.get("id") or ""),
            str(current_location.get("location_id") or ""),
        }:
            return self._display_label(current_location)
        return ""

    def _display_label(self, item: dict[str, Any]) -> str:
        """
        功能：从场景对象字典中提取最适合玩家阅读的名称。
        入参：item（dict[str, Any]）：scene_snapshot 中的对象、出口或位置。
        出参：str，优先 name/label/display_name，缺失时使用 ID 类字段。
        异常：不抛异常；空对象返回空字符串。
        """
        label = str(
            item.get("name")
            or item.get("label")
            or item.get("display_name")
            or item.get("object_id")
            or item.get("entity_id")
            or item.get("item_id")
            or item.get("id")
            or ""
        ).strip()
        return self._clean_action_label(label)

    def _clean_action_label(self, label: str) -> str:
        """
        功能：把 interaction/exit 标签中的动作前缀转成更自然的叙事目标名。
        入参：label（str）：来自场景对象、出口或交互定义的展示名。
        出参：str，去掉常见动作动词后的标签；无法清理时返回原标签。
        异常：不抛异常；空字符串直接返回空字符串。
        """
        cleaned = label.strip()
        for prefix in ("询问", "检查", "查看", "前往", "进入", "和", "与"):
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                return cleaned[len(prefix) :].strip()
        return cleaned

    def _visible_focus_sentence(self, state: dict[str, Any]) -> str:
        """
        功能：把当前场景中的可见 NPC、物品和出口压缩成一句叙事线索。
        入参：state（dict[str, Any]）：当前回合状态快照。
        出参：str，以中文句号开头的补充句；没有线索时返回空字符串。
        异常：不抛异常；字段缺失或类型异常时返回空字符串。
        """
        scene = self._as_mapping(state.get("scene_snapshot"))
        npc_labels = self._labels_from_sequence(scene.get("visible_npcs"))
        item_labels = self._labels_from_sequence(scene.get("visible_items"))
        exit_labels = self._labels_from_sequence(scene.get("exits"))
        parts: list[str] = []
        if npc_labels:
            parts.append(f"可见人物：{'、'.join(npc_labels[:3])}")
        if item_labels:
            parts.append(f"可互动物品：{'、'.join(item_labels[:3])}")
        if exit_labels:
            parts.append(f"可走方向：{'、'.join(exit_labels[:3])}")
        if not parts:
            return ""
        return f" {'；'.join(parts)}。"

    def _labels_from_sequence(self, value: Any) -> list[str]:
        """
        功能：从未知列表中提取最多用于叙事提示的展示名。
        入参：value（Any）：可能为 scene_snapshot 中的对象列表。
        出参：list[str]，去重后的展示名列表。
        异常：不抛异常；非列表输入返回空列表。
        """
        if not isinstance(value, list):
            return []
        labels: list[str] = []
        for item in value:
            label = self._display_label(item) if isinstance(item, dict) else str(item)
            label = label.strip()
            if label and label not in labels:
                labels.append(label)
        return labels

    def _effect_sentence(self, physics_diff: dict[str, Any]) -> str:
        """
        功能：把确定性 physics_diff 中已经发生的状态差异转成简短反馈。
        入参：physics_diff（dict[str, Any]）：主循环结算结果。
        出参：str，以中文句号开头的补充句；没有明确差异时返回空字符串。
        异常：不抛异常；字段缺失时返回空字符串。
        """
        parts: list[str] = []
        hp_delta = self._to_int(physics_diff.get("hp_delta", 0))
        mp_delta = self._to_int(physics_diff.get("mp_delta", 0))
        if hp_delta:
            parts.append(f"生命{'恢复' if hp_delta > 0 else '变化'} {hp_delta:+d}")
        if mp_delta:
            parts.append(f"法力{'恢复' if mp_delta > 0 else '消耗'} {mp_delta:+d}")
        flags = physics_diff.get("state_flags_add")
        if isinstance(flags, list) and flags:
            # 展示边界：physics_diff 内保留机器可读 flag，GM 文本必须转换为玩家可读标签。
            # Story Pack 动态交互有时会产生“中文目标_英文后缀”的临时标记，这里统一收口。
            flag_labels = self._state_flag_labels(flags)
            if flag_labels:
                parts.append(f"状态标记：{'、'.join(flag_labels[:3])}")
        consumed = physics_diff.get("consumed_item_id")
        if consumed:
            parts.append(f"消耗物品：{consumed}")
        if not parts:
            return ""
        return f" {'；'.join(parts)}。"

    def _state_flag_labels(self, flags: list[Any]) -> list[str]:
        """
        功能：把 physics_diff 中的状态 flag 列表转换为去重后的玩家可读标签。
        入参：flags（list[Any]）：状态标记列表，可能混入非字符串或内部协议 key。
        出参：list[str]，优先使用规则配置 label，其次使用动态中文后缀降级；
            纯英文未知项显示为“状态更新”。
        异常：不抛异常；坏数据会被跳过或降级，避免污染 GM 叙事。
        """
        labels: list[str] = []
        for raw_flag in flags:
            label = self._state_flag_label(raw_flag)
            if label and label not in labels:
                labels.append(label)
        return labels

    def _state_flag_label(self, raw_flag: Any) -> str:
        """
        功能：解析单个状态 flag 的玩家展示名。
        入参：raw_flag（Any）：可能为稳定状态 key、已本地化中文标签或动态派生 flag。
        出参：str，返回可直接展示的中文标签；无法识别时返回“状态更新”。
        异常：不抛异常；非字符串或空白值返回空字符串。
        """
        if not isinstance(raw_flag, str):
            return ""
        flag = raw_flag.strip()
        if not flag:
            return ""

        configured_label = self._configured_state_flag_label(flag)
        if configured_label:
            return configured_label

        dynamic_label = self._dynamic_state_flag_label(flag)
        if dynamic_label:
            return dynamic_label

        # 降级路径：已经是中文自然短语时保留；纯内部 key 不直接暴露给玩家。
        if "_" not in flag and self._contains_cjk(flag):
            return flag
        return "状态更新"

    def _configured_state_flag_label(self, flag: str) -> str:
        """
        功能：从主循环 character_status.flags 规则中读取稳定状态 key 的中文标签。
        入参：flag（str）：状态 key。
        出参：str，命中配置返回 label，未命中返回空字符串。
        异常：不抛异常；配置结构异常按未命中处理。
        """
        status_rules = self.rules.get("character_status", {})
        flag_templates = status_rules.get("flags") if isinstance(status_rules, dict) else {}
        if not isinstance(flag_templates, dict):
            return ""
        template = flag_templates.get(flag)
        if not isinstance(template, dict):
            return ""
        label = str(template.get("label") or "").strip()
        return label

    def _dynamic_state_flag_label(self, flag: str) -> str:
        """
        功能：把 Story Pack 动态交互生成的“中文对象_英文后缀”状态转为自然中文。
        入参：flag（str）：状态标记，例如 `潮誓碎片_recovered`。
        出参：str，命中已知后缀返回中文短语，未命中返回空字符串。
        异常：不抛异常；无中文主体或无下划线时返回空。
        """
        if "_" not in flag:
            return ""
        subject, suffix = flag.rsplit("_", 1)
        subject = subject.strip()
        suffix = suffix.strip().lower()
        if not subject or not self._contains_cjk(subject):
            return ""
        suffix_labels = {
            "seen": f"看见{subject}",
            "found": f"发现{subject}",
            "started": f"{subject}已开始",
            "unsealed": f"{subject}已解封",
            "maintained": f"{subject}仍在维持",
            "recovered": f"已找回{subject}",
            "complete": f"{subject}已完成",
            "completed": f"{subject}已完成",
        }
        return suffix_labels.get(suffix, "")

    def _contains_cjk(self, text: str) -> bool:
        """
        功能：判断文本中是否包含中文字符，用于区分玩家可读短语与内部英文 key。
        入参：text（str）：待检查文本。
        出参：bool，包含中日韩统一表意文字时返回 True。
        异常：不抛异常。
        """
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _as_mapping(self, value: Any) -> dict[str, Any]:
        """
        功能：将未知输入安全收敛为字典，避免模板渲染路径被 `Any` 污染。
        入参：value（Any）：可能为 `dict`、`TypedDict` 或其他对象。
        出参：dict[str, Any]，当输入不是字典时返回空字典。
        异常：不抛异常；通过降级为空字典保证渲染链路稳定。
        """
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        return {}

    def _to_int(self, value: Any, default: int = 0) -> int:
        """
        功能：将未知数值安全转换为整数，统一模板渲染数值口径。
        入参：value（Any）：待转换值；default（int，默认 0）：转换失败时的降级值。
        出参：int，转换成功返回真实值，失败返回 default。
        异常：内部捕获 `TypeError/ValueError`，不向上抛出，避免影响主循环响应。
        """
        try:
            return int(value)
        except TypeError, ValueError:
            return default
