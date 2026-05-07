from __future__ import annotations

import logging
import urllib.error

from agents.gm_agent import GMAgent


class _Response:
    """
    功能：为 GM 单测提供最小 urllib 响应替身，隔离真实 Ollama 网络调用。
    入参：body（bytes，默认空）：read() 返回体；lines（list[bytes] | None，默认 None）：流式迭代行。
    出参：上下文管理器对象，支持 read() 与迭代协议。
    异常：不主动抛异常；调用方可通过 monkeypatch 注入异常分支。
    """

    def __init__(self, body: bytes = b"", lines: list[bytes] | None = None) -> None:
        self._body = body
        self._lines = lines or []

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return self._body


class _UnreadableHTTPError(urllib.error.HTTPError):
    """
    功能：模拟响应体读取失败的 HTTPError，覆盖错误日志增强的降级路径。
    入参：url/code/msg/hdrs/fp 遵循 urllib.error.HTTPError 构造约束。
    出参：异常实例。
    异常：read() 固定抛 RuntimeError，用于验证 GM 不被日志读取失败打断。
    """

    def read(self, amt: int | None = None) -> bytes:  # noqa: ARG002
        raise RuntimeError("body unavailable")


def test_gm_render_block_returns_clarification_block() -> None:
    """
    功能：验证澄清回合会直接返回 clarification_question，并生成可点击的下一步建议。
    入参：无，使用内联 state。
    出参：None。
    异常：断言失败表示 GM 澄清输出块契约回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False
    state = {
        "turn_outcome": "clarification",
        "clarification_question": "你想往哪个方向走？",
        "scene_snapshot": {
            "affordances": [{"enabled": True, "user_input": "前往北门"}],
            "suggested_actions": [],
        },
    }

    block = agent.render_block(state)
    assert block.narrative == "你想往哪个方向走？"
    assert block.quick_actions[0] == "前往北门"
    assert block.suggested_next_step == "前往北门"


def test_gm_render_block_returns_invalid_with_failure_reason() -> None:
    """
    功能：验证 invalid 回合会输出失败叙事与 failure_reason，确保玩家可理解失败原因。
    入参：无，使用内联 state。
    出参：None。
    异常：断言失败表示 invalid 输出链路回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False
    state = {
        "is_valid": False,
        "turn_outcome": "invalid",
        "validation_errors": ["目标地点不在当前场景出口中"],
        "scene_snapshot": {
            "affordances": [{"enabled": True, "user_input": "观察周围"}],
            "suggested_actions": [],
        },
    }

    block = agent.render_block(state)
    assert "不在当前场景" in block.narrative
    assert block.failure_reason == "目标地点不在当前场景出口中"
    assert block.suggested_next_step == "观察周围"


def test_gm_llm_prompt_includes_character_status_context() -> None:
    """
    功能：验证 GM LLM 提示词包含角色状态摘要、状态效果和本回合 physics_diff。
    入参：无，使用内联 state。
    出参：None。
    异常：断言失败表示 active_character 状态上下文没有进入叙事模型输入。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    prompt = agent._build_llm_prompt(  # noqa: SLF001
        {
            "turn_id": 1,
            "user_input": "继续前进",
            "turn_outcome": "valid_action",
            "is_valid": True,
            "action_intent": {"type": "move"},
            "physics_diff": {"mp_delta": -1, "state_flags_add": ["moved_recently"]},
            "active_character": {
                "id": "player_01",
                "name": "玩家",
                "status_summary": "受伤、刚刚移动",
                "status_effects": [
                    {
                        "key": "hp_wounded",
                        "label": "受伤",
                        "kind": "resource",
                        "severity": "warning",
                        "description": "生命值低于安全线。",
                    }
                ],
                "status_context": {
                    "resource_state": "hp_wounded",
                    "flags": ["moved_recently"],
                    "prompt_text": "受伤(warning): 生命值低于安全线。",
                },
            },
            "scene_snapshot": {"recent_memory": ""},
        }
    )
    assert "受伤、刚刚移动" in prompt
    assert "state_flags_add" in prompt
    assert "生命值低于安全线" in prompt


def test_gm_quick_actions_prioritize_affordances() -> None:
    """
    功能：验证快捷行动优先使用 affordances，避免 LLM 或兜底动作越权漂移。
    入参：无，使用内联 state。
    出参：None。
    异常：断言失败表示快捷行动优先级回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False
    state = {
        "scene_snapshot": {
            "affordances": [
                {"enabled": True, "user_input": "前往北门"},
                {"enabled": True, "user_input": "询问守卫"},
                {"enabled": True, "user_input": "观察周围"},
            ],
            "suggested_actions": ["继续前进"],
        },
    }
    actions = agent.suggest_quick_actions(state, "叙事文本")
    assert actions[:3] == ["前往北门", "询问守卫", "观察周围"]


def test_gm_quick_actions_drop_unmapped_generated_actions(monkeypatch) -> None:
    """
    功能：验证 LLM/隐藏块生成的快捷行动必须映射到 enabled affordance 才能返回。
    入参：monkeypatch：注入模型输出。
    出参：None。
    异常：断言失败表示 GM 重新允许越权快捷行动进入前端按钮。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "test-model"}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _Response(
            '{"response":"叙事<quick_actions>[\\"凭空飞走\\"]</quick_actions>"}'.encode()
        ),
    )
    state = {
        "scene_snapshot": {
            "affordances": [{"enabled": True, "user_input": "观察周围"}],
        }
    }

    text = agent.render(state)
    actions = agent.suggest_quick_actions(state, text)

    assert actions == ["观察周围"]


def test_gm_fallback_quick_actions_deduplicate_and_limit_to_four() -> None:
    """
    功能：验证兜底快捷行动会去重并限制为 4 条，确保前端按钮数量稳定。
    入参：无，使用内联 state。
    出参：None。
    异常：断言失败表示兜底快捷行动约束失效。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False
    state = {
        "scene_snapshot": {
            "suggested_actions": ["观察周围", "观察周围", "继续前进", "继续前进", "检查背包"],
        },
    }
    actions = agent._fallback_quick_actions(state)  # noqa: SLF001
    assert len(actions) == 4
    assert actions == ["观察周围", "继续前进", "检查背包", "和附近的人交谈"]


def test_gm_render_with_llm_unsupported_provider_has_warning_log(
    caplog,
) -> None:
    """
    功能：验证 GM 在启用 LLM 且 provider 不受支持时会记录告警并回退模板渲染。
    入参：caplog：pytest 日志捕获器。
    出参：None。
    异常：断言失败表示 LLM 降级路径缺失日志证据。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "unknown_provider"}
    caplog.set_level(logging.WARNING, logger="Agent.GM")

    text = agent.render({"is_valid": False, "validation_errors": ["行动未能成立"]})
    assert "GM LLM provider=unknown_provider 不受支持" in caplog.text
    assert text


def test_gm_suggest_quick_actions_llm_failure_has_warning_log(
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证快捷行动 LLM 生成失败时会记录 warning 并回退到场景建议动作。
    入参：monkeypatch、caplog。
    出参：None。
    异常：断言失败表示快捷行动降级路径缺失日志证据。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "test-model", "base_url": "http://localhost:11434"}

    def _raise_url_error(*args, **kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("urllib.request.urlopen", _raise_url_error)
    caplog.set_level(logging.WARNING, logger="Agent.GM")

    state = {
        "scene_snapshot": {
            "affordances": [{"enabled": True, "user_input": "观察周围"}],
            "suggested_actions": ["观察周围", "继续前进"],
        }
    }
    actions = agent.suggest_quick_actions(state, "叙事")
    assert actions == ["观察周围"]
    assert "GM 快捷行动生成失败，已降级为场景建议" in caplog.text


def test_gm_render_with_llm_invalid_payload_logs_reason_and_uses_template(
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证 Ollama 返回非对象 JSON 时记录 reason，并回退到确定性模板叙事。
    入参：monkeypatch、caplog：替换网络响应并捕获 GM warning 日志。
    出参：None。
    异常：断言失败表示 LLM 响应 schema 降级或日志证据回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "test-model"}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Response(b'["bad"]'))
    caplog.set_level(logging.WARNING, logger="Agent.GM")

    text = agent.render(
        {
            "is_valid": False,
            "validation_errors": ["缺少目标"],
            "active_character": {"name": "测试者"},
        }
    )

    assert "测试者的行动未能成立：缺少目标" == text
    assert "reason=invalid_payload_type" in caplog.text
    assert "payload_type=list" in caplog.text


def test_gm_render_with_llm_http_error_body_read_failure_still_logs_fallback(
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证 HTTPError 响应体读取失败时仍记录 LLM 降级日志并返回模板叙事。
    入参：monkeypatch、caplog：注入 HTTPError 与捕获 warning。
    出参：None。
    异常：断言失败表示错误链路日志增强影响了模板降级。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "missing-model", "base_url": "http://ollama"}

    def _raise_http_error(*args, **kwargs):  # noqa: ANN002, ANN003
        raise _UnreadableHTTPError("http://ollama/api/generate", 500, "boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _raise_http_error)
    caplog.set_level(logging.WARNING, logger="Agent.GM")

    text = agent.render({"is_valid": True, "action_intent": {"type": "wait"}})

    assert "停下来片刻" in text
    assert "reason=http_error" in caplog.text
    assert "<failed to read error body: body unavailable>" in caplog.text


def test_gm_stream_response_filters_hidden_blocks_and_keeps_quick_actions(
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证流式响应会过滤 think/quick_actions 隐藏块、跳过坏 JSON 行并保留快捷行动。
    入参：monkeypatch、caplog：注入 JSONL 响应与捕获解析 warning。
    出参：None。
    异常：断言失败表示流式叙事、隐藏块过滤或快捷行动解析回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "stream-model"}
    lines = [
        '{"response":"正文一<think>隐藏"}\n'.encode(),
        b"not-json\n",
        '{"response":"内容</think>正文二<quick_actions>[\\"观察门\\",\\"询问人\\"]"}\n'.encode(),
        '{"response":"</quick_actions>结尾","done":true}\n'.encode(),
    ]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _Response(lines=lines),
    )
    caplog.set_level(logging.WARNING, logger="Agent.GM")
    chunks: list[str] = []
    state = {
        "is_valid": True,
        "action_intent": {"type": "wait"},
        "scene_snapshot": {
            "affordances": [
                {"enabled": True, "user_input": "观察门"},
                {"enabled": True, "user_input": "询问人"},
            ]
        },
    }

    text = agent.render(
        state,
        stream_callback=chunks.append,
    )
    actions = agent.suggest_quick_actions(state, text)

    assert text == "正文一正文二结尾"
    assert chunks == ["正文一", "正文二", "结尾"]
    assert actions == ["观察门", "询问人"]
    assert "GM LLM 流式响应行无法解析，已跳过" in caplog.text


def test_gm_embedded_quick_actions_are_request_local(monkeypatch) -> None:
    """
    功能：验证嵌入式快捷行动绑定到本次 state，不会被同一 GMAgent 的后续请求覆盖。
    入参：monkeypatch：按顺序注入两次非流式 LLM 响应。
    出参：None。
    异常：断言失败表示共享 GMAgent 实例存在跨请求 quick actions 串话风险。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "test-model"}
    responses = iter(
        [
            _Response(
                (
                    '{"response":"甲叙事<quick_actions>[\\"甲行动一\\",\\"甲行动二\\"]'
                    '</quick_actions>"}'
                ).encode()
            ),
            _Response(
                (
                    '{"response":"乙叙事<quick_actions>[\\"乙行动一\\",\\"乙行动二\\"]'
                    '</quick_actions>"}'
                ).encode()
            ),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: next(responses))
    state_a = {"scene_snapshot": {"affordances": [{"enabled": True, "user_input": "甲行动一"}]}}
    state_b = {"scene_snapshot": {"affordances": [{"enabled": True, "user_input": "乙行动一"}]}}

    text_a = agent.render(state_a)
    text_b = agent.render(state_b)
    actions_a = agent.suggest_quick_actions(state_a, text_a)
    actions_b = agent.suggest_quick_actions(state_b, text_b)

    assert text_a == "甲叙事"
    assert text_b == "乙叙事"
    assert actions_a == ["甲行动一"]
    assert actions_b == ["乙行动一"]


def test_gm_stream_callback_failure_is_logged_and_stream_continues(
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证 SSE 片段回调异常会被记录并忽略，避免打断完整叙事读取。
    入参：monkeypatch、caplog：注入流式响应与失败回调日志捕获。
    出参：None。
    异常：断言失败表示回调失败会中断 GM LLM 流式降级策略。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = True
    agent.llm_config = {"provider": "ollama", "model": "stream-model"}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _Response(
            lines=[
                '{"response":"片段一"}\n'.encode(),
                '{"response":"片段二","done":true}\n'.encode(),
            ]
        ),
    )
    caplog.set_level(logging.WARNING, logger="Agent.GM")

    def _broken_callback(_chunk: str) -> None:
        raise RuntimeError("client disconnected")

    text = agent.render(
        {"is_valid": True, "action_intent": {"type": "wait"}},
        _broken_callback,
    )

    assert text == "片段一片段二"
    assert "GM LLM 流式片段回调失败，已忽略" in caplog.text


def test_gm_quick_actions_parser_cleans_response_noise_and_limits_items() -> None:
    """
    功能：验证快捷行动解析会截取 JSON 数组、去重、过滤空值并限制最多 4 条。
    入参：无，使用内联模型文本。
    出参：None。
    异常：断言失败表示 quick actions 输出契约回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    long_action = "沿着非常非常非常非常非常非常非常非常非常长的小路前进"

    actions = agent._parse_quick_actions(  # noqa: SLF001
        f'说明文字 ["观察周围", "观察周围", "", 123, "{long_action}", "检查背包"] 结束'
    )

    assert actions == ["观察周围", "123", long_action[:40], "检查背包"]


def test_gm_affordance_actions_skip_invalid_items_and_use_label_fallback() -> None:
    """
    功能：验证 affordance 快捷行动会跳过禁用/非法项，并在 user_input 缺失时使用 label。
    入参：无，使用内联 scene_snapshot。
    出参：None。
    异常：断言失败表示 affordance 清洗或越权保护回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    state = {
        "scene_snapshot": {
            "affordances": [
                "bad",
                {"enabled": False, "user_input": "禁用行动"},
                {"enabled": True, "label": "查看告示"},
                {"enabled": True, "user_input": "查看告示"},
                {"enabled": True, "user_input": "询问守卫"},
                {"enabled": True, "user_input": "检查背包"},
                {"enabled": True, "user_input": "前往北门"},
                {"enabled": True, "user_input": "额外行动"},
            ]
        }
    }

    actions = agent._affordance_quick_actions(state)  # noqa: SLF001

    assert actions == ["查看告示", "询问守卫", "检查背包", "前往北门"]


def test_gm_render_block_handles_missing_fields_for_invalid_and_clarification() -> None:
    """
    功能：验证 invalid/clarification 的异常输入会降级为稳定叙事、failure_reason 和下一步建议。
    入参：无，使用字段类型异常的内联 state。
    出参：None。
    异常：断言失败表示三态 outcome 异常输入保护回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False

    invalid_block = agent.render_block(
        {
            "is_valid": False,
            "turn_outcome": "invalid",
            "validation_errors": "bad-type",
            "scene_snapshot": {"affordances": "bad-type"},
        }
    )
    clarification_block = agent.render_block(
        {
            "is_valid": False,
            "turn_outcome": "clarification",
            "clarification_question": "",
            "validation_errors": [],
        }
    )

    assert invalid_block.narrative == "旅者的行动未能成立：行动未能成立。"
    assert invalid_block.failure_reason == "行动未能成立。"
    assert invalid_block.suggested_next_step == "观察周围"
    assert clarification_block.narrative == "你能再具体说明一下吗？"
    assert clarification_block.failure_reason == "行动未能成立。"
    assert clarification_block.suggested_next_step == "观察周围"


def test_gm_template_renders_action_branches_with_numeric_fallbacks() -> None:
    """
    功能：验证模板叙事覆盖主要 action_type 分支，并在数字字段异常时使用 0 降级。
    入参：无，使用最小状态集合。
    出参：None。
    异常：断言失败表示 GM 模板字段缺失降级或行动分支回归。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    agent.llm_enabled = False
    base = {"is_valid": True, "active_character": {"name": "旅者"}}

    assert "未能命中 敌人" in agent.render(
        base
        | {
            "action_intent": {"type": "attack", "target_id": "敌人"},
            "physics_diff": {"attack_hit": False, "attack_roll": "bad", "attack_dc": 12},
        }
    )
    assert "造成了 5 点伤害" in agent.render(
        base
        | {
            "action_intent": {"type": "attack", "target_id": "敌人"},
            "physics_diff": {"attack_hit": True, "target_hp_delta": -5},
        }
    )
    assert "与 npc_01 进行交谈" in agent.render(
        base | {"action_intent": {"type": "talk", "target_id": "npc_01"}, "physics_diff": {}}
    )
    assert "前往了 gate" in agent.render(
        base
        | {
            "action_intent": {"type": "move", "parameters": {"location_id": "gate"}},
            "physics_diff": {"mp_delta": -2},
        }
    )
    assert "观察周围：古老大厅" in agent.render(
        base
        | {
            "action_intent": {"type": "observe"},
            "scene_snapshot": {"current_location": {"description": "古老大厅"}},
        }
    )
    assert "恢复了 3 点生命与 4 点法力" in agent.render(
        base | {"action_intent": {"type": "rest"}, "physics_diff": {"hp_delta": 3, "mp_delta": 4}}
    )
    assert "使用了 potion，恢复了 7 点生命" in agent.render(
        base
        | {
            "action_intent": {"type": "use_item", "parameters": {"item_id": "potion"}},
            "physics_diff": {"hp_delta": 7},
        }
    )
    assert "完成了 custom 行动" in agent.render(base | {"action_intent": {"type": "custom"}})
