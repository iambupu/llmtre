import json
import logging
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# 忽略本地网络代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from llama_index.core import Document, Settings, SimpleDirectoryReader
from llama_index.core.extractors import BaseExtractor
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import JSONNodeParser, MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import BaseNode
from llama_index.readers.file import DocxReader, PandasExcelReader, PDFReader, XMLReader
from pydantic import Field

from .builders.graph_builder import PropertyGraphIndexBuilder

# 导入子构建器
from .builders.vector_builder import VectorIndexBuilder

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
INDEX_DIR = os.path.join(BASE_DIR, "knowledge_base", "indices")

logger = logging.getLogger("RAGManager.Builder")

class MinerUDirectoryReader:
    """专用于解析 MinerU 导出的标准目录结构"""
    def load_data(self, dir_path: str) -> list[Document]:
        """
        功能：加载配置或数据资源。
        入参：dir_path。
        出参：List[Document]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        dir_p = Path(dir_path)
        md_files = list(dir_p.glob("*.md"))
        if not md_files:
            return []

        main_md = md_files[0]
        with open(main_md, encoding="utf-8") as f:
            content = f.read()

        img_paths = re.findall(r'!\[.*?\]\((.*?)\)', content)
        abs_img_paths = [str((dir_p / p).resolve()) for p in img_paths if (dir_p / p).exists()]

        metadata = {
            "file_name": main_md.name,
            "is_mineru_parsed": True,
            "extracted_images": abs_img_paths,
        }

        # 尝试加载 MinerU 元数据
        for jf in dir_p.glob("*.json"):
            try:
                with open(jf, encoding="utf-8") as f:
                    meta = json.load(f)
                    if isinstance(meta, dict):
                        if "title" in meta:
                            metadata["mineru_title"] = meta["title"]
            except Exception:
                continue

        return [Document(text=content, metadata=metadata)]

class RulebookMetadataExtractor(BaseExtractor):
    """LLM 自动特征打分器"""
    llm: Any = Field(description="LLM instance")
    scoring_prompt: str = Field(description="Scoring prompt template")

    async def aextract(self, nodes: Sequence[BaseNode]) -> list[dict[str, Any]]:
        """
        功能：执行 `aextract` 相关业务逻辑。
        入参：nodes。
        出参：List[Dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        metadata_list = []
        for node in nodes:
            try:
                res = self.llm.complete(f"{self.scoring_prompt}\n\n文本:\n{node.get_content()}")
                text = str(res).strip()
                score, tags = (text.split("|", 1) + ["5", "uncategorized"])[:2]
                metadata_list.append(
                    {"lore_importance_score": int(score), "lore_tags": tags.split(",")}
                )
            except Exception:
                metadata_list.append({"lore_importance_score": 5, "lore_tags": ["error"]})
        return metadata_list

class IndexBuilder:
    """核心构建指挥官：协调数据加载、切片以及 Vector/Graph 索引的构建"""

    def __init__(self, config: dict[str, Any]):
        """
        功能：初始化对象状态与依赖。
        入参：config。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.config = config
        self.vector_builder = VectorIndexBuilder(INDEX_DIR)
        self.graph_builder = PropertyGraphIndexBuilder(INDEX_DIR, config)

    def _get_file_extractors(self) -> dict[str, Any]:
        """
        功能：执行 `_get_file_extractors` 相关业务逻辑。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        extractors = {
            ".docx": DocxReader(),
            ".doc": DocxReader(),
            ".xml": XMLReader(),
            ".xlsx": PandasExcelReader(),
            ".xls": PandasExcelReader(),
        }
        key = self.config.get("llama_cloud_api_key", "")
        if key and "YOUR_LLAMA_CLOUD" not in key:
            from llama_parse import LlamaParse
            extractors[".pdf"] = LlamaParse(api_key=key, result_type="markdown")
        else:
            extractors[".pdf"] = PDFReader()
        return extractors

    def build_all(self, rules_path: str | None = None) -> None:
        """
        功能：执行全量索引构建流程：根据规则按需构建向量与图谱索引。
        入参：rules_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("--- 开始 RAG 复合索引构建任务 ---")
        if rules_path is None:
            rules_path = os.path.join(BASE_DIR, "config", "rag_import_rules.json")

        # 1. 分类摄取文档
        vector_docs, graph_docs = self._load_classified_documents(rules_path)

        if not vector_docs:
            logger.warning("没有找到可用于构建向量库的文档。")
            return

        # 2. 属性图谱构建 (仅针对标记了 enable_graph 的文档)
        if graph_docs:
            self.graph_builder.build(graph_docs)
        else:
            logger.info("未发现标记为图谱化的文档，跳过图谱构建。")

        # 3. 智能切片 (向量库针对所有文档进行语义索引)
        nodes = self._parse_nodes(vector_docs)

        # 4. LLM 打分 (如果开启)
        if self.config.get("metadata_extraction", {}).get("enable_custom_scoring") and Settings.llm:
            nodes = self._run_llm_scoring(nodes)

        # 5. 向量索引构建
        self.vector_builder.build(nodes)

        logger.info("--- 所有 RAG 索引任务执行完毕 ---")

    def _load_classified_documents(self, rules_path: str) -> tuple[list[Document], list[Document]]:
        """
        功能：从规则中加载文档并根据 enable_graph 进行分类。
        入参：rules_path。
        出参：tuple[List[Document], List[Document]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not os.path.exists(rules_path):
            logger.warning("未找到导入规则，默认扫描 docs 目录并仅构建向量索引。")
            docs = SimpleDirectoryReader(
                DOCS_DIR,
                file_extractor=self._get_file_extractors(),
            ).load_data()
            return docs, []

        all_vector_docs: list[Document] = []
        all_graph_docs: list[Document] = []

        with open(rules_path, encoding="utf-8") as f:
            groups = json.load(f).get("groups", [])

        for g in groups:
            group_name = g["group_name"]
            is_graph_enabled = g.get("enable_graph", False)

            group_docs: list[Document] = []
            for p in g.get("file_paths", []):
                fp = os.path.join(BASE_DIR, p)
                if not os.path.exists(fp):
                    continue

                if os.path.isdir(fp):
                    docs = MinerUDirectoryReader().load_data(fp)
                else:
                    docs = SimpleDirectoryReader(
                        input_files=[fp],
                        file_extractor=self._get_file_extractors(),
                    ).load_data()

                for d in docs:
                    d.metadata.update({
                        "rag_group_name": group_name,
                        "rag_custom_tags": ",".join(g.get("tags", []))
                    })
                group_docs.extend(docs)

            all_vector_docs.extend(group_docs)
            if is_graph_enabled:
                logger.info(f"分组 [{group_name}] 已标记为图谱构建。")
                all_graph_docs.extend(group_docs)

        return all_vector_docs, all_graph_docs

    def _parse_nodes(self, documents: list[Document]) -> list[BaseNode]:
        """
        功能：执行 `_parse_nodes` 相关业务逻辑。
        入参：documents。
        出参：List[BaseNode]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        md_parser = MarkdownNodeParser()
        json_parser = JSONNodeParser()
        txt_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        final_nodes: list[BaseNode] = []
        for doc in documents:
            ext = doc.metadata.get("file_name", "").lower()
            if ext.endswith(".md"):
                nodes = md_parser.get_nodes_from_documents([doc])
            elif ext.endswith(".json"):
                nodes = json_parser.get_nodes_from_documents([doc])
            else:
                nodes = txt_parser.get_nodes_from_documents([doc])
            for node in nodes:
                node.metadata.update(doc.metadata)
            final_nodes.extend(nodes)
        return final_nodes

    def _run_llm_scoring(self, nodes: list[BaseNode]) -> list[BaseNode]:
        """
        功能：执行 `_run_llm_scoring` 相关业务逻辑。
        入参：nodes。
        出参：List[BaseNode]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        pipeline = IngestionPipeline(
            transformations=[
                RulebookMetadataExtractor(
                    llm=Settings.llm,
                    scoring_prompt=self.config["metadata_extraction"]["scoring_prompt"],
                )
            ]
        )
        scored_nodes = pipeline.run(nodes=nodes)
        return list(scored_nodes)
