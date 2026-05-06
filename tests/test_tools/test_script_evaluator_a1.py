from __future__ import annotations

import logging

from tools.sandbox.script_evaluator import ScriptEvaluator


class _FakeLLM:
    """
    功能：模拟 LLM complete 接口，返回固定响应或抛出异常。
    入参：response（object）：complete 返回值；error（Exception | None）：可选异常。
    出参：测试辅助对象，记录收到的 prompt。
    异常：error 不为 None 时 complete 抛出该异常。
    """

    def __init__(self, response: object, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> object:
        """
        功能：记录 prompt 并返回预置响应。
        入参：prompt（str）：LLM 判定提示词。
        出参：object，预置响应。
        异常：当 error 不为 None 时抛出该异常。
        """
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


def test_python_condition_allows_safe_expression_eval() -> None:
    """
    功能：验证表达式判定可读取上下文并返回布尔结果。
    入参：无。
    出参：None。
    异常：断言失败表示安全执行环境或返回值标准化回归。
    """
    context = {"hp": 9, "mp": 3}
    evaluator = ScriptEvaluator()

    ok = evaluator.evaluate_python_condition(
        "hp > 0 and mp >= 3",
        context,
    )

    assert ok is True


def test_python_condition_defaults_false_without_result() -> None:
    """
    功能：验证表达式为假时标准化返回 False。
    入参：无。
    出参：None。
    异常：断言失败表示 result 缺失降级回归。
    """
    assert ScriptEvaluator().evaluate_python_condition("1 > 2", {}) is False


def test_python_condition_rejects_unsafe_import_and_logs_error(caplog) -> None:
    """
    功能：验证不允许语句级语法（如 import），并记录错误日志。
    入参：caplog。
    出参：None。
    异常：断言失败表示脚本异常捕获或日志证据回归。
    """
    with caplog.at_level(logging.ERROR, logger="ScriptEvaluator"):
        ok = ScriptEvaluator().evaluate_python_condition("__import__('os')", {})

    assert ok is False
    assert "Python 脚本执行失败" in caplog.text
    assert "__import__" in caplog.text


def test_llm_condition_without_llm_logs_warning_and_returns_false(caplog) -> None:
    """
    功能：验证未配置 LLM 时返回 False 并记录 warning。
    入参：caplog。
    出参：None。
    异常：断言失败表示 LLM 缺失降级回归。
    """
    with caplog.at_level(logging.WARNING, logger="ScriptEvaluator"):
        ok = ScriptEvaluator().evaluate_llm_condition("已完成任务", "历史")

    assert ok is False
    assert "LLM 未配置" in caplog.text


def test_llm_condition_parses_true_false_and_builds_prompt() -> None:
    """
    功能：验证 LLM 响应会按 TRUE/FALSE 文本标准化，并将条件与历史写入 prompt。
    入参：无。
    出参：None。
    异常：断言失败表示 LLM 判定解析或 prompt 构造回归。
    """
    true_llm = _FakeLLM(" true ")
    false_llm = _FakeLLM("FALSE")

    assert ScriptEvaluator(true_llm).evaluate_llm_condition("条件 A", "历史 A") is True
    assert ScriptEvaluator(false_llm).evaluate_llm_condition("条件 B", "历史 B") is False
    assert "条件 A" in true_llm.prompts[0]
    assert "历史 A" in true_llm.prompts[0]


def test_llm_condition_exception_logs_error_and_returns_false(caplog) -> None:
    """
    功能：验证 LLM complete 异常会记录错误并降级 False。
    入参：caplog。
    出参：None。
    异常：断言失败表示 LLM 异常降级回归。
    """
    with caplog.at_level(logging.ERROR, logger="ScriptEvaluator"):
        ok = ScriptEvaluator(_FakeLLM("", RuntimeError("llm down"))).evaluate_llm_condition(
            "条件",
            "历史",
        )

    assert ok is False
    assert "LLM 判定执行失败: llm down" in caplog.text
