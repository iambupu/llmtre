from __future__ import annotations

import asyncio
import json
from typing import Any

from llama_index.core import Document
from llama_index.core.schema import TextNode

import tools.rag.index_builder as index_builder
from tools.rag.index_builder import IndexBuilder, MinerUDirectoryReader, RulebookMetadataExtractor


class _FakeVectorBuilder:
    """
    功能：替代真实向量索引构建器，记录 build 入参并避免触碰磁盘索引。
    入参：index_dir（str）：构建目录，占位保留。
    出参：_FakeVectorBuilder。
    异常：无显式异常。
    """

    def __init__(self, index_dir: str) -> None:
        self.index_dir = index_dir
        self.built_nodes: list[Any] = []

    def build(self, nodes: list[Any]) -> None:
        self.built_nodes = nodes


class _FakeGraphBuilder:
    """
    功能：替代真实图谱索引构建器，记录图谱文档入参。
    入参：index_dir（str）；config（dict[str, Any]）。
    出参：_FakeGraphBuilder。
    异常：无显式异常。
    """

    def __init__(self, index_dir: str, config: dict[str, Any]) -> None:
        self.index_dir = index_dir
        self.config = config
        self.built_docs: list[Document] = []

    def build(self, docs: list[Document]) -> None:
        self.built_docs = docs


class _FakeDirectoryReader:
    """
    功能：替代 SimpleDirectoryReader，按目录或文件输入返回稳定 Document。
    入参：*args/**kwargs：保持与 LlamaIndex reader 构造兼容。
    出参：_FakeDirectoryReader。
    异常：无显式异常。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def load_data(self) -> list[Document]:
        file_name = "default.txt"
        input_files = self.kwargs.get("input_files")
        if isinstance(input_files, list) and input_files:
            file_name = str(input_files[0]).split("\\")[-1].split("/")[-1]
        return [Document(text="reader text", metadata={"file_name": file_name})]


class _FakeLLM:
    """
    功能：提供可控 complete 响应或异常，覆盖元数据打分成功与降级路径。
    入参：response（str | Exception）：返回文本或待抛异常。
    出参：_FakeLLM。
    异常：当 response 为 Exception 时 complete 抛出该异常。
    """

    def __init__(self, response: str | Exception) -> None:
        self.response = response

    def complete(self, prompt: str) -> str:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _patch_builders(monkeypatch) -> None:
    """
    功能：把 IndexBuilder 的真实子构建器替换为测试替身。
    入参：monkeypatch（pytest.MonkeyPatch）：pytest 补丁器。
    出参：None。
    异常：补丁失败时由 pytest 抛出。
    """
    monkeypatch.setattr(index_builder, "VectorIndexBuilder", _FakeVectorBuilder)
    monkeypatch.setattr(index_builder, "PropertyGraphIndexBuilder", _FakeGraphBuilder)


def test_mineru_directory_reader_loads_markdown_images_and_json_metadata(tmp_path) -> None:
    """
    功能：验证 MinerU 目录读取 Markdown、存在图片和 JSON title 元数据，坏 JSON 会跳过。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 MinerU 前处理回归。
    """
    (tmp_path / "image.png").write_bytes(b"png")
    (tmp_path / "main.md").write_text(
        "正文\n![图](image.png)\n![缺失](missing.png)",
        encoding="utf-8",
    )
    (tmp_path / "meta.json").write_text(json.dumps({"title": "规则书"}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")

    docs = MinerUDirectoryReader().load_data(str(tmp_path))

    assert len(docs) == 1
    assert docs[0].text.startswith("正文")
    assert docs[0].metadata["is_mineru_parsed"] is True
    assert docs[0].metadata["mineru_title"] == "规则书"
    assert len(docs[0].metadata["extracted_images"]) == 1


def test_mineru_directory_reader_returns_empty_without_markdown(tmp_path) -> None:
    """
    功能：验证 MinerU 目录没有 Markdown 文件时返回空列表。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示空目录边界回归。
    """
    assert MinerUDirectoryReader().load_data(str(tmp_path)) == []


def test_mineru_directory_reader_ignores_non_dict_or_titleless_metadata(tmp_path) -> None:
    """
    功能：验证 MinerU JSON 元数据为非 dict 或缺少 title 时不会污染文档 metadata。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 MinerU 元数据清洗边界回归。
    """
    (tmp_path / "main.md").write_text("正文", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (tmp_path / "no_title.json").write_text(json.dumps({"author": "x"}), encoding="utf-8")

    docs = MinerUDirectoryReader().load_data(str(tmp_path))

    assert len(docs) == 1
    assert docs[0].metadata["is_mineru_parsed"] is True
    assert "mineru_title" not in docs[0].metadata


def test_rulebook_metadata_extractor_scores_and_falls_back_on_llm_error() -> None:
    """
    功能：验证 LLM 打分成功解析 score/tags，异常时使用默认 error 元数据。
    入参：无。
    出参：None。
    异常：断言失败表示自定义元数据打分降级回归。
    """
    node = TextNode(text="规则文本")
    success_extractor = RulebookMetadataExtractor(
        llm=_FakeLLM("9|combat,magic"),
        scoring_prompt="打分",
    )
    failing_extractor = RulebookMetadataExtractor(
        llm=_FakeLLM(RuntimeError("llm down")),
        scoring_prompt="打分",
    )

    success = asyncio.run(success_extractor.aextract([node]))
    fallback = asyncio.run(failing_extractor.aextract([node]))

    assert success == [{"lore_importance_score": 9, "lore_tags": ["combat", "magic"]}]
    assert fallback == [{"lore_importance_score": 5, "lore_tags": ["error"]}]


def test_rulebook_metadata_extractor_falls_back_on_malformed_score() -> None:
    """
    功能：验证 LLM 返回不可解析 score 时使用默认 error 元数据。
    入参：无。
    出参：None。
    异常：断言失败表示 LLM 打分格式降级回归。
    """
    node = TextNode(text="规则文本")
    extractor = RulebookMetadataExtractor(
        llm=_FakeLLM("bad-score|combat"),
        scoring_prompt="打分",
    )

    result = asyncio.run(extractor.aextract([node]))

    assert result == [{"lore_importance_score": 5, "lore_tags": ["error"]}]


def test_load_classified_documents_defaults_to_docs_scan_when_rules_missing(
    monkeypatch,
    tmp_path,
) -> None:
    """
    功能：验证规则文件缺失时默认扫描 docs 目录，且只返回向量文档。
    入参：monkeypatch；tmp_path。
    出参：None。
    异常：断言失败表示默认扫描降级路径回归。
    """
    _patch_builders(monkeypatch)
    monkeypatch.setattr(index_builder, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(index_builder.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(index_builder, "SimpleDirectoryReader", _FakeDirectoryReader)
    builder = IndexBuilder(config={})

    vector_docs, graph_docs = builder._load_classified_documents(str(tmp_path / "missing.json"))

    assert len(vector_docs) == 1
    assert vector_docs[0].metadata["file_name"] == "default.txt"
    assert graph_docs == []


def test_load_classified_documents_applies_group_metadata_and_graph_split(
    monkeypatch,
    tmp_path,
) -> None:
    """
    功能：验证规则分组会跳过不存在文件、读取 MinerU 目录/普通文件，并按 enable_graph 分类。
    入参：monkeypatch；tmp_path。
    出参：None。
    异常：断言失败表示规则导入分类或元数据补充回归。
    """
    _patch_builders(monkeypatch)
    monkeypatch.setattr(index_builder, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(index_builder, "SimpleDirectoryReader", _FakeDirectoryReader)
    mineru_dir = tmp_path / "mineru"
    mineru_dir.mkdir()
    (mineru_dir / "main.md").write_text("# MinerU", encoding="utf-8")
    normal_file = tmp_path / "normal.txt"
    normal_file.write_text("normal", encoding="utf-8")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "group_name": "core",
                        "enable_graph": True,
                        "tags": ["规则", "核心"],
                        "file_paths": ["mineru", "normal.txt", "missing.txt"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    builder = IndexBuilder(config={})

    vector_docs, graph_docs = builder._load_classified_documents(str(rules_path))

    assert len(vector_docs) == 2
    assert len(graph_docs) == 2
    assert {doc.metadata["rag_group_name"] for doc in vector_docs} == {"core"}
    assert {doc.metadata["rag_custom_tags"] for doc in vector_docs} == {"规则,核心"}


def test_parse_nodes_chooses_parser_by_file_extension(monkeypatch) -> None:
    """
    功能：验证 Markdown/JSON/普通文本会分别进入对应 parser，并继承文档 metadata。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 parser 选择或 metadata 传播回归。
    """
    _patch_builders(monkeypatch)
    builder = IndexBuilder(config={})
    docs = [
        Document(text="# 标题\n正文", metadata={"file_name": "rules.md", "source": "md"}),
        Document(text='{"name": "规则"}', metadata={"file_name": "rules.json", "source": "json"}),
        Document(text="普通文本", metadata={"file_name": "rules.txt", "source": "txt"}),
    ]

    nodes = builder._parse_nodes(docs)

    assert {node.metadata["source"] for node in nodes} == {"md", "json", "txt"}


def test_build_all_skips_graph_when_no_graph_docs_and_builds_vector(monkeypatch) -> None:
    """
    功能：验证 build_all 在无图谱文档时跳过 graph_builder，并把解析节点交给 vector_builder。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 build_all 编排路径回归。
    """
    _patch_builders(monkeypatch)
    builder = IndexBuilder(config={"metadata_extraction": {"enable_custom_scoring": False}})
    docs = [Document(text="普通文本", metadata={"file_name": "rules.txt"})]
    monkeypatch.setattr(builder, "_load_classified_documents", lambda _path: (docs, []))

    builder.build_all("rules.json")

    assert builder.graph_builder.built_docs == []
    assert builder.vector_builder.built_nodes
