from __future__ import annotations

from typing import Any

import pytest

import tools.rag as rag_module
from tools.rag import RAGManager


class _FakeBuilder:
    """
    功能：替代 IndexBuilder，记录 update_index 委托参数。
    入参：config（dict[str, Any]）：RAG 配置。
    出参：_FakeBuilder。
    异常：无显式异常。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.rules_path: str | None = None

    def build_all(self, rules_path: str | None = None) -> None:
        self.rules_path = rules_path


class _FakeRetriever:
    """
    功能：替代 UnifiedRetriever，提供普通/只读查询成功和失败分支。
    入参：config（dict[str, Any]）：RAG 配置。
    出参：_FakeRetriever。
    异常：query 文本为 raise 时抛 RuntimeError。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def query(self, query: str) -> str:
        if query == "raise":
            raise RuntimeError("retriever down")
        return f"query:{query}"

    def query_readonly(self, query: str) -> str:
        if query == "raise":
            raise RuntimeError("readonly down")
        return f"readonly:{query}"


class _FakeLLM:
    """
    功能：替代 LLM 客户端，记录构造参数并提供 ping 行为。
    入参：**kwargs：模型构造参数。
    出参：_FakeLLM。
    异常：complete 文本为 fail 时抛 RuntimeError。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def complete(self, text: str) -> str:
        if text == "fail":
            raise RuntimeError("llm failed")
        return "pong"


class _FakeEmbedding:
    """
    功能：替代 Embedding 客户端，记录构造参数并提供 ping 行为。
    入参：**kwargs：模型构造参数。
    出参：_FakeEmbedding。
    异常：输入 fail 时抛 RuntimeError。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def get_text_embedding(self, text: str) -> list[float]:
        if text == "fail":
            raise RuntimeError("embedding failed")
        return [0.1]


class _FakeSettings:
    """
    功能：替代 LlamaIndex Settings，避免测试假模型被真实 setter 校验。
    入参：无。
    出参：_FakeSettings。
    异常：无显式异常。
    """

    llm: Any = None
    embed_model: Any = None


def _patch_manager_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：替换 RAGManager 的重依赖构造器，隔离真实索引和模型客户端。
    入参：monkeypatch（pytest.MonkeyPatch）：pytest 补丁器。
    出参：None。
    异常：补丁失败时由 pytest 抛出。
    """
    monkeypatch.setattr(rag_module, "IndexBuilder", _FakeBuilder)
    monkeypatch.setattr(rag_module, "UnifiedRetriever", _FakeRetriever)


def test_rag_manager_load_config_missing_and_present(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证配置缺失返回空字典，配置存在时按 YAML 加载。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示配置加载边界回归。
    """
    _patch_manager_deps(monkeypatch)
    missing = tmp_path / "missing.yml"
    config_path = tmp_path / "rag_config.yml"
    config_path.write_text("llm:\n  provider: ollama\n", encoding="utf-8")

    monkeypatch.setattr(rag_module, "CONFIG_PATH", str(missing))
    assert RAGManager().config == {}

    monkeypatch.setattr(rag_module, "CONFIG_PATH", str(config_path))
    manager = RAGManager()
    assert manager.config["llm"]["provider"] == "ollama"


def test_rag_manager_initializes_default_model_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证模型初始化会为 Ollama/OpenAI 兼容 provider 设置默认 base_url 和 dummy key。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示模型配置映射回归。
    """
    _patch_manager_deps(monkeypatch)
    monkeypatch.setattr(rag_module, "Settings", _FakeSettings)
    monkeypatch.setattr(rag_module, "Ollama", _FakeLLM)
    monkeypatch.setattr(rag_module, "OpenAI", _FakeLLM)
    monkeypatch.setattr(rag_module, "OllamaEmbedding", _FakeEmbedding)
    monkeypatch.setattr(rag_module, "OpenAIEmbedding", _FakeEmbedding)
    monkeypatch.setattr(
        RAGManager,
        "_load_config",
        lambda _self: {
            "llm": {"provider": "qwen", "model": "qwen-test"},
            "embedding": {"provider": "ollama", "model": "embed-test"},
        },
    )

    manager = RAGManager()

    assert manager._get_default_base_url("qwen") == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert _FakeSettings.llm.kwargs["api_key"] == "sk-dummy"
    assert _FakeSettings.llm.kwargs["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert _FakeSettings.embed_model.kwargs["model_name"] == "embed-test"


def test_rag_manager_delegates_update_and_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 update_index/query_lore/query_lore_readonly 会委托到底层 builder/retriever。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 RAGManager 外观层委托回归。
    """
    _patch_manager_deps(monkeypatch)
    manager = RAGManager()

    manager.update_index("rules.json")

    assert manager.builder.rules_path == "rules.json"
    assert manager.query_lore("世界观") == "query:世界观"
    assert manager.query_lore_readonly("规则") == "readonly:规则"


def test_rag_manager_query_exceptions_degrade_with_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 retriever 查询异常时外观层返回稳定失败文案并记录异常日志。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 RAG 查询异常降级与日志证据回归。
    """
    _patch_manager_deps(monkeypatch)
    manager = RAGManager()
    caplog.set_level("ERROR", logger="RAGManager")

    assert manager.query_lore("raise") == "检索失败：retriever down"
    assert manager.query_lore_readonly("raise") == "检索失败：readonly down"
    assert "RAG 查询失败" in caplog.text
    assert "RAG 只读查询失败" in caplog.text


def test_rag_manager_probe_raises_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证显式模型连通性探测会把 LLM/Embedding 异常包装为 ConnectionError。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示模型探测错误链路回归。
    """
    _patch_manager_deps(monkeypatch)
    monkeypatch.setattr(rag_module, "Settings", _FakeSettings)
    manager = RAGManager()
    _FakeSettings.llm = _FakeLLM()
    _FakeSettings.embed_model = _FakeEmbedding()

    _FakeSettings.llm.complete = lambda _text: (_ for _ in ()).throw(RuntimeError("bad llm"))
    with pytest.raises(ConnectionError, match="LLM 失败"):
        manager.ensure_model_connectivity()

    _FakeSettings.llm = None
    _FakeSettings.embed_model.get_text_embedding = lambda _text: (_ for _ in ()).throw(
        RuntimeError("bad embedding")
    )
    with pytest.raises(ConnectionError, match="Embedding 失败"):
        manager.ensure_model_connectivity()
