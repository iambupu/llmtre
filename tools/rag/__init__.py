import logging
import os
from typing import Any, cast

import yaml

# 忽略本地网络代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from llama_index.core import Settings
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai import OpenAI

from .index_builder import IndexBuilder
from .unified_retriever import UnifiedRetriever

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "rag_config.yml")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# 配置日志
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "rag_manager.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("RAGManager")

class RAGManager:
    """RAG 系统统一入口类"""
    def __init__(self, probe_on_init: bool = False):
        """
        功能：初始化对象状态与依赖。
        入参：probe_on_init。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.config = self._load_config()
        self._initialize_models()
        if probe_on_init:
            self.ensure_model_connectivity()
        self.builder = IndexBuilder(self.config)
        self.retriever = UnifiedRetriever(self.config)

    def _load_config(self) -> dict[str, Any]:
        """
        功能：执行 `_load_config` 相关业务逻辑。
        入参：无。
        出参：Dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not os.path.exists(CONFIG_PATH):
            return {}
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _get_default_base_url(self, provider: str) -> str:
        """
        功能：执行 `_get_default_base_url` 相关业务逻辑。
        入参：provider。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        default_urls = {
            "deepseek": "https://api.deepseek.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "grok": "https://api.x.ai/v1",
            "glm": "https://open.bigmodel.cn/api/paas/v4",
            "kimi": "https://api.moonshot.cn/v1",
            "openai": "https://api.openai.com/v1",
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "siliconflow": "https://api.siliconflow.cn/v1",
            "ollama": "http://localhost:11434",
            "lmstudio": "http://localhost:1234/v1",
        }
        return default_urls.get(provider, "")

    def _initialize_models(self) -> None:
        """
        功能：执行 `_initialize_models` 相关业务逻辑。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        llm_cfg = self.config.get("llm", {})
        embed_cfg = self.config.get("embedding", {})
        enable_custom_scoring = self.config.get("metadata_extraction", {}).get(
            "enable_custom_scoring",
            False,
        )

        if enable_custom_scoring or llm_cfg.get("provider"):
            provider = str(llm_cfg.get("provider", "")).lower()
            model = str(llm_cfg.get("model", ""))
            api_key = str(llm_cfg.get("api_key", ""))
            base_url = str(llm_cfg.get("base_url", "") or self._get_default_base_url(provider))

            if provider == "ollama":
                Settings.llm = Ollama(model=model, base_url=base_url)
            else:
                Settings.llm = OpenAI(model=model, api_key=api_key or "sk-dummy", api_base=base_url)
        else:
            Settings.llm = cast(Any, None)

        if embed_cfg.get("provider"):
            provider = str(embed_cfg.get("provider", "")).lower()
            model = str(embed_cfg.get("model", ""))
            api_key = str(embed_cfg.get("api_key", ""))
            base_url = str(embed_cfg.get("base_url", "") or self._get_default_base_url(provider))

            if provider == "ollama":
                Settings.embed_model = OllamaEmbedding(model_name=model, base_url=base_url)
            else:
                Settings.embed_model = OpenAIEmbedding(
                    model=model,
                    api_key=api_key or "sk-dummy",
                    api_base=base_url,
                )

    def ensure_model_connectivity(self) -> None:
        """
        功能：显式执行模型连通性探测，不在请求路径构造阶段强制触发。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("测试模型连通性...")
        if Settings.llm:
            try:
                Settings.llm.complete("ping")
            except Exception as error:
                raise ConnectionError(f"LLM 失败: {error}") from error
        if Settings.embed_model:
            try:
                Settings.embed_model.get_text_embedding("ping")
            except Exception as error:
                raise ConnectionError(f"Embedding 失败: {error}") from error

    def update_index(self, rules_path: str | None = None) -> None:
        """
        功能：更新目标状态。
        入参：rules_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.builder.build_all(rules_path)

    def query_lore(self, query: str) -> str:
        """
        功能：执行 `query_lore` 相关业务逻辑。
        入参：query。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return self.retriever.query(query)

    def query_lore_readonly(self, query: str) -> str:
        """
        功能：执行 `query_lore_readonly` 相关业务逻辑。
        入参：query。
        出参：str。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return self.retriever.query_readonly(query)

if __name__ == "__main__":
    manager = RAGManager(probe_on_init=True)
    manager.update_index()
