from __future__ import annotations

from typing import Any

from llama_index.core import Document
from llama_index.core.schema import TextNode

import tools.rag.builders.graph_builder as graph_builder
import tools.rag.builders.vector_builder as vector_builder
from tools.rag.builders.graph_builder import PropertyGraphIndexBuilder
from tools.rag.builders.vector_builder import VectorIndexBuilder


class _FakeStorageContext:
    """
    功能：记录 persist 调用目录。
    入参：无。
    出参：_FakeStorageContext。
    异常：无显式异常。
    """

    def __init__(self) -> None:
        self.persist_dir: str | None = None

    def persist(self, persist_dir: str) -> None:
        self.persist_dir = persist_dir


class _FakeVectorStoreIndex:
    """
    功能：替代 VectorStoreIndex，记录节点并提供 storage_context。
    入参：nodes（list[Any]）：待构建节点。
    出参：_FakeVectorStoreIndex。
    异常：无显式异常。
    """

    last_instance: _FakeVectorStoreIndex | None = None

    def __init__(self, nodes: list[Any]) -> None:
        self.nodes = nodes
        self.storage_context = _FakeStorageContext()
        _FakeVectorStoreIndex.last_instance = self


class _FailingVectorStoreIndex:
    """
    功能：模拟向量索引构建异常。
    入参：nodes（list[Any]）：待构建节点。
    出参：无。
    异常：固定抛 RuntimeError。
    """

    def __init__(self, nodes: list[Any]) -> None:  # noqa: ARG002
        raise RuntimeError("vector failed")


class _FakeGraphIndex:
    """
    功能：替代 PropertyGraphIndex，记录 from_documents 入参与 persist 目录。
    入参：无。
    出参：_FakeGraphIndex。
    异常：当 should_fail 为 True 时 from_documents 抛 RuntimeError。
    """

    should_fail = False
    last_kwargs: dict[str, Any] = {}
    last_instance: _FakeGraphIndex | None = None

    def __init__(self) -> None:
        self.storage_context = _FakeStorageContext()
        _FakeGraphIndex.last_instance = self

    @classmethod
    def from_documents(cls, documents: list[Document], **kwargs: Any) -> _FakeGraphIndex:
        if cls.should_fail:
            raise RuntimeError("graph failed")
        cls.last_kwargs = {"documents": documents, **kwargs}
        return cls()


class _FakeSchemaExtractor:
    """
    功能：替代 SchemaLLMPathExtractor，记录 LLM 与 prompt 参数。
    入参：llm（Any）；extract_prompt（str）。
    出参：_FakeSchemaExtractor。
    异常：无显式异常。
    """

    def __init__(self, llm: Any, extract_prompt: str) -> None:
        self.llm = llm
        self.extract_prompt = extract_prompt


class _FakeSettings:
    """
    功能：替代 LlamaIndex Settings，只暴露 llm 字段。
    入参：无。
    出参：_FakeSettings。
    异常：无显式异常。
    """

    llm: Any = object()


def test_vector_builder_persists_index_for_nodes_and_empty_nodes(tmp_path, monkeypatch) -> None:
    """
    功能：验证向量构建器会把普通节点和空节点交给索引构造器，并调用 persist。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示 vector persist 主路径回归。
    """
    monkeypatch.setattr(vector_builder, "VectorStoreIndex", _FakeVectorStoreIndex)
    builder = VectorIndexBuilder(str(tmp_path))
    nodes = [TextNode(text="规则文本")]

    index = builder.build(nodes)
    empty_index = builder.build([])

    assert index.nodes == nodes
    assert index.storage_context.persist_dir == str(tmp_path / "vector")
    assert empty_index.nodes == []
    assert empty_index.storage_context.persist_dir == str(tmp_path / "vector")


def test_vector_builder_logs_and_reraises_build_error(tmp_path, monkeypatch, caplog) -> None:
    """
    功能：验证向量构建失败时记录错误日志并向上抛出，避免静默丢索引。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 vector 异常日志链路回归。
    """
    monkeypatch.setattr(vector_builder, "VectorStoreIndex", _FailingVectorStoreIndex)
    builder = VectorIndexBuilder(str(tmp_path))
    caplog.set_level("ERROR", logger="RAGManager.VectorBuilder")

    try:
        builder.build([TextNode(text="规则文本")])
    except RuntimeError as error:
        assert str(error) == "vector failed"
    else:
        raise AssertionError("VectorIndexBuilder.build 应该抛出 RuntimeError")

    assert "向量索引构建失败: vector failed" in caplog.text


def test_graph_builder_skips_when_disabled_or_llm_missing(tmp_path, monkeypatch, caplog) -> None:
    """
    功能：验证图谱构建在配置禁用或 LLM 缺失时跳过，并留下日志证据。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 graph 跳过分支回归。
    """
    monkeypatch.setattr(graph_builder, "Settings", _FakeSettings)
    caplog.set_level("INFO", logger="RAGManager.GraphBuilder")

    disabled = PropertyGraphIndexBuilder(str(tmp_path), {"property_graph": {"enabled": False}})
    assert disabled.build([Document(text="规则")]) is None
    assert "属性图谱构建已禁用" in caplog.text

    _FakeSettings.llm = None
    enabled = PropertyGraphIndexBuilder(str(tmp_path), {"property_graph": {"enabled": True}})
    assert enabled.build([Document(text="规则")]) is None
    assert "未配置 LLM，无法进行属性图谱关系提取" in caplog.text


def test_graph_builder_persists_graph_index_with_configured_prompt(tmp_path, monkeypatch) -> None:
    """
    功能：验证图谱构建会使用配置 prompt 创建 extractor，并持久化 graph 索引。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示 graph persist 主路径回归。
    """
    _FakeSettings.llm = object()
    _FakeGraphIndex.should_fail = False
    monkeypatch.setattr(graph_builder, "Settings", _FakeSettings)
    monkeypatch.setattr(graph_builder, "SchemaLLMPathExtractor", _FakeSchemaExtractor)
    monkeypatch.setattr(graph_builder, "PropertyGraphIndex", _FakeGraphIndex)
    builder = PropertyGraphIndexBuilder(
        str(tmp_path),
        {"property_graph": {"enabled": True, "extraction_prompt": "抽取关系: {text}"}},
    )
    docs = [Document(text="规则")]

    graph_index = builder.build(docs)
    extractor = _FakeGraphIndex.last_kwargs["kg_extractors"][0]

    assert graph_index is _FakeGraphIndex.last_instance
    assert extractor.extract_prompt == "抽取关系: {text}"
    assert graph_index.storage_context.persist_dir == str(tmp_path / "graph")


def test_graph_builder_logs_warning_and_returns_none_on_build_error(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    """
    功能：验证图谱构建异常时记录降级警告并返回 None，不阻断全量 RAG 构建。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 graph 异常降级链路或日志级别回归。
    """
    _FakeSettings.llm = object()
    _FakeGraphIndex.should_fail = True
    monkeypatch.setattr(graph_builder, "Settings", _FakeSettings)
    monkeypatch.setattr(graph_builder, "SchemaLLMPathExtractor", _FakeSchemaExtractor)
    monkeypatch.setattr(graph_builder, "PropertyGraphIndex", _FakeGraphIndex)
    builder = PropertyGraphIndexBuilder(str(tmp_path), {"property_graph": {"enabled": True}})
    caplog.set_level("WARNING", logger="RAGManager.GraphBuilder")

    assert builder.build([Document(text="规则")]) is None
    assert "属性图谱构建失败，已降级跳过: graph failed" in caplog.text
