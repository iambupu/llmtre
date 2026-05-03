from __future__ import annotations

import json
import logging
import urllib.error

from agents.nlu_agent import NLUAgent


class _FakeHTTPResponse:
    """
    功能：模拟 urllib 返回对象，向 NLU 提供可控的 JSON 响应体。
    入参：payload_text（str）：要返回给调用方的文本。
    出参：_FakeHTTPResponse，可作为 context manager 使用。
    异常：不抛异常。
    """

    def __init__(self, payload_text: str) -> None:
        """
        功能：保存预置响应文本。
        入参：payload_text（str）：read() 返回内容。
        出参：None。
        异常：不抛异常。
        """
        self._payload_text = payload_text

    def __enter__(self) -> _FakeHTTPResponse:
        """
        功能：支持 with 上下文协议。
        入参：无。
        出参：_FakeHTTPResponse。
        异常：不抛异常。
        """
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """
        功能：支持 with 上下文协议并不吞异常。
        入参：exc_type/exec/traceback：上下文退出参数。
        出参：bool，固定 False 表示不吞异常。
        异常：不抛异常。
        """
        return False

    def read(self) -> bytes:
        """
        功能：返回编码后的响应体字节。
        入参：无。
        出参：bytes，UTF-8 编码内容。
        异常：不抛异常。
        """
        return self._payload_text.encode("utf-8")


def _build_agent_with_llm() -> NLUAgent:
    """
    功能：构造开启 LLM fallback 的 NLUAgent，便于复用测试基线。
    入参：无。
    出参：NLUAgent，已启用 llm_enabled 且规则关键词为空。
    异常：不抛异常。
    """
    agent = NLUAgent(rules={"nlu": {"action_keywords": {}}})
    agent.llm_enabled = True
    agent.llm_config = {
        "provider": "ollama",
        "model": "qwen3:8b",
        "base_url": "http://localhost:11434",
        "temperature": 0.0,
    }
    agent.llm_timeout_seconds = 1
    return agent


def test_nlu_llm_provider_unsupported_returns_none_with_warning(caplog) -> None:
    """
    功能：验证 provider 非 ollama 时直接降级返回 None，并输出可诊断日志。
    入参：caplog（pytest fixture）：日志捕获器。
    出参：None。
    异常：断言失败表示配置降级分支或日志契约回归。
    """
    agent = _build_agent_with_llm()
    agent.llm_config["provider"] = "openai"
    with caplog.at_level(logging.WARNING, logger="Agent.NLU"):
        parsed = agent.parse("做点什么", context={"id": "player_01"})
    assert parsed is None
    assert "provider=openai 不受支持" in caplog.text


def test_nlu_llm_model_missing_returns_none_with_warning(caplog) -> None:
    """
    功能：验证 LLM 未配置 model 时降级返回 None，并输出配置缺失日志。
    入参：caplog（pytest fixture）：日志捕获器。
    出参：None。
    异常：断言失败表示 model 校验分支退化。
    """
    agent = _build_agent_with_llm()
    agent.llm_config["model"] = ""
    with caplog.at_level(logging.WARNING, logger="Agent.NLU"):
        parsed = agent.parse("继续前进", context={"id": "player_01"})
    assert parsed is None
    assert "未配置 model" in caplog.text


def test_nlu_llm_url_error_is_logged_and_downgraded(monkeypatch, caplog) -> None:
    """
    功能：验证 urllib URL 错误会记录 reason=url_error 日志并降级为未识别动作。
    入参：monkeypatch（pytest fixture）：函数替换工具；caplog（pytest fixture）：日志捕获器。
    出参：None。
    异常：断言失败表示异常降级路径或日志证据缺失。
    """
    agent = _build_agent_with_llm()

    def _raise_url_error(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise_url_error)
    with caplog.at_level(logging.WARNING, logger="Agent.NLU"):
        parsed = agent.parse("试试法术", context={"id": "player_01"})
    assert parsed is None
    assert "reason=url_error" in caplog.text
    assert "connection refused" in caplog.text


def test_nlu_llm_invalid_payload_type_logs_reason(monkeypatch, caplog) -> None:
    """
    功能：验证 LLM 返回非对象 JSON 时触发 invalid_payload_type 日志并返回 None。
    入参：monkeypatch（pytest fixture）：函数替换工具；caplog（pytest fixture）：日志捕获器。
    出参：None。
    异常：断言失败表示 payload 类型校验分支退化。
    """
    agent = _build_agent_with_llm()
    response_text = json.dumps(["not", "object"], ensure_ascii=False)

    def _fake_urlopen(*_args, **_kwargs):
        return _FakeHTTPResponse(response_text)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="Agent.NLU"):
        parsed = agent.parse("检查情况", context={"id": "player_01"})
    assert parsed is None
    assert "reason=invalid_payload_type" in caplog.text


def test_nlu_llm_schema_validation_failed_logs_reason(monkeypatch, caplog) -> None:
    """
    功能：验证 LLM 输出非法动作类型时触发 schema_validation_failed 并降级。
    入参：monkeypatch（pytest fixture）：函数替换工具；caplog（pytest fixture）：日志捕获器。
    出参：None。
    异常：断言失败表示 schema 收敛保护或日志契约回归。
    """
    agent = _build_agent_with_llm()
    response_text = json.dumps(
        {"response": "{\"type\":\"teleport\",\"parameters\":{}}"},
        ensure_ascii=False,
    )

    def _fake_urlopen(*_args, **_kwargs):
        return _FakeHTTPResponse(response_text)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    with caplog.at_level(logging.WARNING, logger="Agent.NLU"):
        parsed = agent.parse("传送到城堡", context={"id": "player_01"})
    assert parsed is None
    assert "reason=schema_validation_failed" in caplog.text
