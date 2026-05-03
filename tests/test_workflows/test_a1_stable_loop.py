from __future__ import annotations

import asyncio

from core.event_bus import EventBus
from game_workflows.main_event_loop import MainEventLoop
from state.tools.db_initializer import DBInitializer
from tools.entity.entity_probes import EntityProbes
from tools.sqlite_db.db_updater import DBUpdater


class DummyRAGBridge:
    """
    功能：为 A1 稳态回归提供只读 RAG 替身，避免测试依赖真实索引。
    入参：无。
    出参：DummyRAGBridge 实例。
    异常：构造阶段不抛异常。
    """

    def build_snapshot(self, query: str) -> dict[str, object]:
        """
        功能：返回固定 RAG 快照。
        入参：query（str）：主循环构造的查询文本。
        出参：dict[str, object]，最小 RAG 状态。
        异常：不抛异常。
        """
        return {
            "rag_enabled": True,
            "rag_ready": True,
            "rag_query": query,
            "rag_context": "",
            "rag_error": "",
        }


def build_a1_loop(tmp_path) -> MainEventLoop:
    """
    功能：构建隔离数据库上的 A1 主循环测试实例。
    入参：tmp_path（Path）：pytest 临时目录。
    出参：MainEventLoop。
    异常：数据库初始化失败时向上抛出。
    """
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()
    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")
    loop = MainEventLoop(
        event_bus,
        rag_bridge=DummyRAGBridge(),
        db_updater=db_updater,
        entity_probes=entity_probes,
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    return loop


def test_a1_ten_turns_llm_off_have_scene_affordances_and_response(tmp_path) -> None:
    """
    功能：验证 LLM 关闭时 A1 主循环可连续执行十回合并持续返回场景可操作信息。
    入参：tmp_path（pytest fixture）：隔离数据库路径。
    出参：None，通过断言表达验收条件。
    异常：断言失败表示 A1 稳态闭环或降级路径破损。
    """
    loop = build_a1_loop(tmp_path)
    inputs = [
        "观察周围",
        "检查周围",
        "等待片刻",
        "短暂休息",
        "继续前进",
        "观察周围",
        "检查周围",
        "等待片刻",
        "短暂休息",
        "观察周围",
    ]

    for user_input in inputs:
        result = asyncio.run(loop.run(user_input))
        scene = result["scene_snapshot"]
        assert result["final_response"]
        assert result["quick_actions"]
        assert scene is not None
        assert scene["schema_version"] == "scene_snapshot.v2"
        assert scene["scene_objects"]
        assert scene["interaction_slots"]
        assert scene["affordances"]
