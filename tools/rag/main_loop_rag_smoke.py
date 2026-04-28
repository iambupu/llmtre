import os
from typing import Any

from llama_index.core import Settings

from state.tools.db_initializer import DBInitializer
from tools.rag import RAGManager

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")
VECTOR_DOCSTORE_PATH = os.path.join(
    BASE_DIR,
    "knowledge_base",
    "indices",
    "vector",
    "docstore.json",
)


def _assert_ollama_bge_m3(manager: RAGManager) -> None:
    """
    功能：执行 `_assert_ollama_bge_m3` 相关业务逻辑。
    入参：manager。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    embed_cfg: dict[str, Any] = manager.config.get("embedding", {})
    provider = str(embed_cfg.get("provider", "")).lower()
    model = str(embed_cfg.get("model", "")).lower()
    if provider != "ollama" or model != "bge-m3":
        raise RuntimeError(
            "嵌入模型配置不符合要求，当前需要 ollama/bge-m3。"
            f"实际为 provider={provider}, model={model}"
        )


def main() -> None:
    """
    功能：执行 `main` 相关业务逻辑。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    if not os.path.exists(DB_PATH):
        DBInitializer(DB_PATH).initialize_db()

    manager = RAGManager()
    _assert_ollama_bge_m3(manager)

    if not os.path.exists(VECTOR_DOCSTORE_PATH):
        manager.update_index()

    if Settings.embed_model is None:
        raise RuntimeError("Settings.embed_model 未初始化。")
    Settings.embed_model.get_text_embedding("main-loop-rag-smoke")
    print("RAG_SMOKE_OK")


if __name__ == "__main__":
    main()
