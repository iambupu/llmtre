"""
功能：覆盖 main event loop 的回归测试。
"""

import asyncio
import json
import sqlite3
from pathlib import Path

from core.event_bus import EventBus
from game_workflows.async_watchers import (
    NoOpOuterLoopBridge,
    OuterLoopBridge,
    WorkflowOuterLoopBridge,
)
from game_workflows.main_event_loop import MainEventLoop
from state.contracts.turn import TurnRequestContext
from state.tools.db_initializer import DBInitializer
from tools.entity.entity_probes import EntityProbes
from tools.packs.registry import StoryPackRegistry
from tools.sqlite_db.db_updater import DBUpdater

_A1_TEST_SCENE_LOCATIONS = {
    "village_square": {
        "id": "village_square",
        "name": "测试村庄广场",
        "description": "A1 主循环测试用入口场景。",
        "exits": [
            {
                "location_id": "forest_edge",
                "label": "测试森林边缘",
                "aliases": ["森林", "林子里", "树林", "边缘"],
            },
            {
                "location_id": "ruin_hall",
                "label": "测试废墟大厅",
                "aliases": ["废墟", "遗迹", "大厅"],
            },
        ],
        "visible_items": [],
        "visible_npcs": [],
    },
    "forest_edge": {
        "id": "forest_edge",
        "name": "测试森林边缘",
        "description": "A1 主循环测试用移动目标场景。",
        "exits": [
            {
                "location_id": "village_square",
                "label": "测试村庄广场",
                "aliases": ["村庄", "广场", "村子"],
            }
        ],
        "visible_items": [],
        "visible_npcs": ["goblin_01"],
    },
    "ruin_hall": {
        "id": "ruin_hall",
        "name": "测试废墟大厅",
        "description": "A1 主循环测试用备用场景。",
        "exits": [
            {
                "location_id": "village_square",
                "label": "测试村庄广场",
                "aliases": ["村庄", "广场", "村子"],
            }
        ],
        "visible_items": [],
        "visible_npcs": [],
    },
    "unknown": {
        "id": "unknown",
        "name": "未知地点",
        "description": "测试降级场景。",
        "exits": [],
        "visible_items": [],
        "visible_npcs": [],
    },
}


def _install_a1_test_content(loop: MainEventLoop) -> None:
    """
    功能：向测试临时库注入 A1 行为测试所需的场景、目标与物品夹具。
    入参：loop（MainEventLoop）：已连接临时 SQLite 的主循环实例。
    出参：None。
    异常：SQLite 写入失败时向上抛出，表示测试夹具不可用。
    """
    # 意图：生产种子不再包含固定游戏内容；这些世界对象仅作为测试局部夹具存在。
    loop.rules.setdefault("scene_defaults", {})["locations"] = json.loads(
        json.dumps(_A1_TEST_SCENE_LOCATIONS, ensure_ascii=False)
    )
    loop.nlu_agent.nlu_rules["item_aliases"] = {"health_potion_01": ["药水", "potion"]}
    with sqlite3.connect(loop.db_updater.db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO items
            (item_id, name, description, item_type, min_strength, min_agility,
             min_intelligence, effects_json, hooks_json, weight, rarity, usage_limit, is_stackable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "health_potion_01",
                "测试治疗药水",
                "A1 主循环测试用消耗品。",
                "consumable",
                0,
                0,
                0,
                json.dumps([{"target_attribute": "hp", "value": 20}], ensure_ascii=False),
                "{}",
                0.0,
                "common",
                -1,
                True,
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO items
            (item_id, name, description, item_type, min_strength, min_agility,
             min_intelligence, effects_json, hooks_json, weight, rarity, usage_limit, is_stackable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "iron_sword_01",
                "测试铁剑",
                "A1 主循环测试用武器。",
                "weapon",
                8,
                0,
                0,
                json.dumps([{"target_attribute": "attack", "value": 5}], ensure_ascii=False),
                "{}",
                0.0,
                "common",
                -1,
                False,
            ),
        )
        connection.execute(
            """
            UPDATE entities_active
            SET current_location_id = ?, strength = ?, agility = ?, intelligence = ?,
                constitution = ?, hp = ?, max_hp = ?, mp = ?, max_mp = ?
            WHERE entity_id = ?
            """,
            ("village_square", 12, 10, 11, 10, 100, 100, 50, 50, "player_01"),
        )
        connection.execute("DELETE FROM inventory_active WHERE owner_id = ?", ("player_01",))
        connection.executemany(
            """
            INSERT INTO inventory_active(owner_id, item_id, quantity)
            VALUES (?, ?, ?)
            """,
            [
                ("player_01", "iron_sword_01", 1),
                ("player_01", "health_potion_01", 1),
            ],
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO entities_active
            (entity_id, name, entity_type, description, strength, agility, intelligence,
             constitution, hp, max_hp, mp, max_mp, traits_json, social_relations_json,
             current_location_id, behavior_pattern, state_flags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "goblin_01",
                "测试地精",
                "monster",
                "A1 主循环测试用攻击目标。",
                6,
                12,
                4,
                8,
                30,
                30,
                0,
                0,
                "[]",
                "{}",
                "forest_edge",
                "aggressive",
                "[]",
            ),
        )
        connection.commit()


def build_loop(tmp_path):
    """
    功能：提供 build loop 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
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
        # 测试隔离边界：默认使用临时目录下的 Agent 上下文，避免读取仓库根长期记忆文件。
        agent_context_dir=tmp_path / ".agent_context",
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    _install_a1_test_content(loop)
    # A2 旧版别名兼容：恢复 A1 时期 target_aliases 和 location_aliases，
    # 确保确定性 NLU 能解析 "攻击地精" 等价短语。
    loop.nlu_agent.nlu_rules["target_aliases"]["goblin_01"] = ["地精", "goblin"]
    loop.nlu_agent.nlu_rules["location_aliases"] = {
        "r_entrance": "废墟入口",
        "ruins_entrance": "废墟入口",
        "遗迹入口": "废墟入口",
        "森林": "forest_edge",
        "在路上": "forest_edge",
    }
    return loop


class CollectingOuterBridge(OuterLoopBridge):
    """
    功能：组织 CollectingOuterBridge 相关测试场景。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：CollectingOuterBridge 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def __init__(self):
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.state_events = []
        self.turn_events = []
        self.world_events = []

    async def emit_state_changed(self, event):
        """
        功能：提供 emit state changed 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.state_events.append(event)

    async def emit_turn_ended(self, event):
        """
        功能：提供 emit turn ended 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.turn_events.append(event)

    async def emit_world_evolution(self, event):
        """
        功能：提供 emit world evolution 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.world_events.append(event)


class FailingOuterBridge(OuterLoopBridge):
    """
    功能：组织 FailingOuterBridge 相关测试场景。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：FailingOuterBridge 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    async def emit_state_changed(self, event):
        """
        功能：提供 emit state changed 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("outer unavailable")

    async def emit_turn_ended(self, event):
        """
        功能：提供 emit turn ended 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("outer unavailable")

    async def emit_world_evolution(self, event):
        """
        功能：提供 emit world evolution 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("outer unavailable")


class PartialFailingOuterBridge(OuterLoopBridge):
    """
    功能：组织 PartialFailingOuterBridge 相关测试场景。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：PartialFailingOuterBridge 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def __init__(self):
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.turn_events = []
        self.world_events = []

    async def emit_state_changed(self, event):
        """
        功能：提供 emit state changed 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("state_changed failed")

    async def emit_turn_ended(self, event):
        """
        功能：提供 emit turn ended 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.turn_events.append(event)

    async def emit_world_evolution(self, event):
        """
        功能：提供 emit world evolution 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.world_events.append(event)


def build_loop_with_outer(tmp_path, outer_bridge):
    """
    功能：提供 build loop with outer 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
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
        outer_bridge=outer_bridge,
        db_updater=db_updater,
        entity_probes=entity_probes,
        # 测试隔离边界：默认使用临时目录下的 Agent 上下文，避免读取仓库根长期记忆文件。
        agent_context_dir=tmp_path / ".agent_context",
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    _install_a1_test_content(loop)
    # A2 旧版别名兼容：恢复 A1 时期 target_aliases 和 location_aliases，
    # 确保确定性 NLU 能解析 "攻击地精" 等价短语。
    loop.nlu_agent.nlu_rules["target_aliases"]["goblin_01"] = ["地精", "goblin"]
    loop.nlu_agent.nlu_rules["location_aliases"] = {
        "r_entrance": "废墟入口",
        "ruins_entrance": "废墟入口",
        "遗迹入口": "废墟入口",
        "森林": "forest_edge",
        "在路上": "forest_edge",
    }
    return loop


class DummyRAGBridge:
    """
    功能：组织 DummyRAGBridge 相关测试场景。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：DummyRAGBridge 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def __init__(self, ready: bool = True):
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.ready = ready

    def build_snapshot(self, query: str):
        """
        功能：提供 build snapshot 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        if self.ready:
            return {
                "rag_enabled": True,
                "rag_ready": True,
                "rag_query": query,
                "rag_context": "规则片段",
                "rag_error": "",
            }
        return {
            "rag_enabled": True,
            "rag_ready": False,
            "rag_query": query,
            "rag_context": "",
            "rag_error": "rag unavailable",
        }


def build_loop_with_rag(tmp_path, rag_bridge):
    """
    功能：提供 build loop with rag 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()
    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")
    loop = MainEventLoop(
        event_bus,
        rag_bridge=rag_bridge,
        db_updater=db_updater,
        entity_probes=entity_probes,
        # 测试隔离边界：默认使用临时目录下的 Agent 上下文，避免读取仓库根长期记忆文件。
        agent_context_dir=tmp_path / ".agent_context",
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    _install_a1_test_content(loop)
    # A2 旧版别名兼容：恢复 A1 时期 target_aliases 和 location_aliases，
    # 确保确定性 NLU 能解析 "攻击地精" 等价短语。
    loop.nlu_agent.nlu_rules["target_aliases"]["goblin_01"] = ["地精", "goblin"]
    loop.nlu_agent.nlu_rules["location_aliases"] = {
        "r_entrance": "废墟入口",
        "ruins_entrance": "废墟入口",
        "遗迹入口": "废墟入口",
        "森林": "forest_edge",
        "在路上": "forest_edge",
    }
    return loop


def test_main_event_loop_can_be_initialized(tmp_path):
    """
    功能：验证 main event loop can be initialized 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    assert loop.graph is not None


def test_main_event_loop_uses_workflow_outer_bridge_by_default(tmp_path):
    """
    功能：验证 main event loop uses workflow outer bridge by default 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    assert isinstance(loop.outer_bridge, WorkflowOuterLoopBridge)
    result = asyncio.run(loop.run("观察周围"))
    assert result["outer_emit_result"]["status"] == "ok"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


def test_main_event_loop_success_path_updates_state(tmp_path):
    """
    功能：验证 main event loop success path updates state 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("攻击地精"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "attack"
    assert result["physics_diff"]["attack_hit"] is True
    assert result["physics_diff"]["attack_dc"] == 16
    assert result["physics_diff"]["attack_roll"] >= result["physics_diff"]["attack_dc"]
    assert 1 <= result["physics_diff"]["damage_roll"] <= 6
    assert result["physics_diff"]["target_hp_delta"] < 0
    assert result["turn_id"] == 1
    assert result["final_response"]

    goblin = loop.entity_probes.get_character_stats("goblin_01")
    assert goblin is not None
    assert goblin["hp"] == 30 + result["physics_diff"]["target_hp_delta"]


def test_main_event_loop_turn_id_is_monotonic_across_turns(tmp_path):
    """
    功能：验证 main event loop turn id is monotonic across turns 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    first = asyncio.run(loop.run("观察周围"))
    second = asyncio.run(loop.run("观察周围"))

    assert first["turn_id"] == 1
    assert second["turn_id"] == 2


def test_main_event_loop_returns_controlled_failure_for_unknown_input(tmp_path):
    """
    功能：验证 main event loop returns controlled failure for unknown input 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("天气真不错"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    assert result["clarification_question"]
    assert result["should_advance_turn"] is False
    assert result["should_write_story_memory"] is False
    assert result["final_response"]


def test_main_event_loop_noop_outer_bridge_reports_skipped(tmp_path):
    """
    功能：验证 NoOp 外环桥接器会在回合结果中明确标记 skipped/noop，便于 service trace 映射。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 noop 外环结果契约回归。
    """
    loop = build_loop_with_outer(tmp_path, NoOpOuterLoopBridge())

    result = asyncio.run(loop.run("观察周围"))

    assert result["outer_emit_result"] == {"status": "skipped", "detail": {"mode": "noop"}}


def test_main_event_loop_request_context_overrides_runtime_inputs(tmp_path):
    """
    功能：验证 request_context 会覆盖角色、沙盒和会话记忆，保证 API 层追踪字段进入主循环。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示请求级上下文桥接回归。
    """
    loop = build_loop(tmp_path)
    context = TurnRequestContext(
        trace_id="trc_ctx_001",
        request_id="req_ctx_001",
        session_id="sess_ctx_001",
        character_id="player_01",
        sandbox_mode=False,
        recent_memory="上一回合摘要",
    )

    result = asyncio.run(
        loop.run(
            "观察周围",
            initial_character_id="ignored_character",
            is_sandbox_mode=True,
            recent_memory="ignored memory",
            request_context=context,
        )
    )

    assert result["trace_id"] == "trc_ctx_001"
    assert result["request_id"] == "req_ctx_001"
    assert result["session_id"] == "sess_ctx_001"
    assert result["active_character_id"] == "player_01"
    assert result["scene_snapshot"]["recent_memory"] == "上一回合摘要"


def test_main_event_loop_mounts_agent_context_memory(tmp_path, caplog):
    """
    功能：验证主循环在无 session_id 的兼容路径会把全局 MEMORY.md 挂载到 recent_memory。
    入参：tmp_path（pytest fixture）：临时数据库和 Agent 上下文目录；caplog：日志捕获器。
    出参：None。
    异常：断言失败表示 Agent 长期记忆没有进入智能体上下文。
    """
    context_dir = tmp_path / ".agent_context"
    context_dir.mkdir()
    (context_dir / "MEMORY.md").write_text(
        "# 长期记忆\n玩家在旧矿洞救下了向导。\n",
        encoding="utf-8",
    )
    loop = build_loop(tmp_path)
    loop.agent_context_dir = context_dir
    caplog.set_level("INFO", logger="Agent.Context")

    result = asyncio.run(loop.run("观察周围", recent_memory="第1回合：抵达营地"))

    recent_memory = result["scene_snapshot"]["recent_memory"]
    assert "第1回合：抵达营地" in recent_memory
    assert "玩家在旧矿洞救下了向导" in recent_memory
    assert "Agent 上下文记忆加载成功" in caplog.text


def test_main_event_loop_mounts_session_scoped_agent_context_memory(tmp_path, caplog):
    """
    功能：验证主循环按 request_context.session_id 挂载对应会话长期记忆，避免多会话串记忆。
    入参：tmp_path（pytest fixture）：临时数据库和 Agent 上下文目录；caplog：日志捕获器。
    出参：None。
    异常：断言失败表示 `.agent_context` 会话级隔离失效。
    """
    context_dir = tmp_path / ".agent_context"
    (context_dir / "sessions" / "sess_ctx_alpha").mkdir(parents=True)
    (context_dir / "sessions" / "sess_ctx_beta").mkdir(parents=True)
    (context_dir / "MEMORY.md").write_text("# 全局\n全局旧记忆不应注入。\n", encoding="utf-8")
    (context_dir / "sessions" / "sess_ctx_alpha" / "MEMORY.md").write_text(
        "# 会话长期记忆\n玩家在甲会话救下了向导。\n",
        encoding="utf-8",
    )
    (context_dir / "sessions" / "sess_ctx_beta" / "MEMORY.md").write_text(
        "# 会话长期记忆\n玩家在乙会话激怒了守卫。\n",
        encoding="utf-8",
    )
    loop = build_loop(tmp_path)
    loop.agent_context_dir = context_dir
    request_context = TurnRequestContext(
        trace_id="trc_ctx_alpha",
        request_id="req_ctx_alpha",
        session_id="sess_ctx_alpha",
        character_id="player_01",
        sandbox_mode=False,
        recent_memory="第1回合：抵达营地",
    )
    caplog.set_level("INFO", logger="Agent.Context")

    result = asyncio.run(loop.run("观察周围", request_context=request_context))

    recent_memory = result["scene_snapshot"]["recent_memory"]
    assert "第1回合：抵达营地" in recent_memory
    assert "甲会话救下了向导" in recent_memory
    assert "乙会话" not in recent_memory
    assert "全局旧记忆" not in recent_memory
    assert "Agent 上下文记忆加载成功" in caplog.text


def test_main_event_loop_prefers_request_long_term_memory_over_file(tmp_path):
    """
    功能：验证 Web 请求携带数据库长期记忆时，主循环不再重复读取会话文件镜像。
    入参：tmp_path（pytest fixture）：临时数据库和 Agent 上下文目录。
    出参：None。
    异常：断言失败表示数据库长期记忆和文件兼容路径发生重复注入。
    """
    context_dir = tmp_path / ".agent_context"
    (context_dir / "sessions" / "sess_ctx_db").mkdir(parents=True)
    (context_dir / "sessions" / "sess_ctx_db" / "MEMORY.md").write_text(
        "# 会话长期记忆\n文件镜像不应在数据库上下文存在时注入。\n",
        encoding="utf-8",
    )
    loop = build_loop(tmp_path)
    loop.agent_context_dir = context_dir
    request_context = TurnRequestContext(
        trace_id="trc_ctx_db",
        request_id="req_ctx_db",
        session_id="sess_ctx_db",
        character_id="player_01",
        sandbox_mode=False,
        recent_memory="第1回合：抵达营地",
        long_term_memory="## 长期叙事记忆\n- 玩家曾向艾拉承诺寻找银叶徽记。",
    )

    result = asyncio.run(loop.run("观察周围", request_context=request_context))

    recent_memory = result["scene_snapshot"]["recent_memory"]
    assert "玩家曾向艾拉承诺寻找银叶徽记" in recent_memory
    assert "文件镜像不应" not in recent_memory


def test_main_event_loop_emits_minimal_outer_events(tmp_path):
    """
    功能：验证 main event loop emits minimal outer events 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert len(outer.state_events) == 1
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    assert outer.state_events[0].entity_id == "player_01"
    assert outer.turn_events[0].turn_id == 1
    assert outer.world_events[0].time_passed_minutes == loop.outer_world_minutes_per_turn
    assert result["outer_emit_result"]["status"] == "ok"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


def test_main_event_loop_outer_failure_does_not_break_turn(tmp_path):
    """
    功能：验证 main event loop outer failure does not break turn 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop_with_outer(tmp_path, FailingOuterBridge())

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["final_response"]
    assert result["outer_emit_result"]["status"] == "failed"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


def test_main_event_loop_partial_outer_failure_does_not_block_following_events(tmp_path):
    """
    功能：验证 main event loop partial outer failure does not block following events 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    outer = PartialFailingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    pending = loop.db_updater.list_pending_outer_events(limit=10)
    assert pending
    assert pending[0]["event_name"] == "state_changed"


def test_main_event_loop_overflow_outbox_only_enqueues_supported_event_types(tmp_path):
    """
    功能：验证 A1 同步外环投递策略下，不再走后台任务溢出分支，也不会写入 outbox 补偿事件。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示仍残留旧的后台溢出行为。
    """
    loop = build_loop(tmp_path)
    loop.outer_max_pending_tasks = 0

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    with sqlite3.connect(loop.db_updater.db_path) as conn:
        rows = conn.execute("SELECT event_name FROM outer_event_outbox").fetchall()
    event_names = {str(row[0]) for row in rows}
    assert "turn_batch" not in event_names
    assert event_names == set()


def test_main_event_loop_populates_world_snapshot_from_rag_bridge(tmp_path):
    """
    功能：验证 main event loop populates world snapshot from rag bridge 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop_with_rag(tmp_path, DummyRAGBridge(ready=True))

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["world_snapshot"]["rag_ready"] is True
    assert result["world_snapshot"]["rag_context"] == "规则片段"


def test_main_event_loop_rag_unavailable_does_not_break_main_logic(tmp_path):
    """
    功能：验证 main event loop rag unavailable does not break main logic 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop_with_rag(tmp_path, DummyRAGBridge(ready=False))

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["world_snapshot"]["rag_ready"] is False
    assert result["final_response"]


def test_main_event_loop_routes_writes_through_event_bus(tmp_path):
    """
    功能：验证 main event loop routes writes through event bus 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    emitted_events: list[str] = []
    original_emit = loop.event_bus.emit

    def tracking_emit(event_name, state):
        """
        功能：提供 tracking emit 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        emitted_events.append(event_name)
        return original_emit(event_name, state)

    loop.event_bus.emit = tracking_emit

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["write_results"]
    assert "on_state_write_pre" in emitted_events
    assert "on_state_write_post" in emitted_events
    assert "on_action_post" in emitted_events


def test_main_event_loop_move_updates_location_and_flag_without_mp_cost(tmp_path):
    """
    功能：验证普通移动只更新位置和移动标记，不再默认消耗法力。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("前往森林"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "move"
    assert result["physics_diff"]["location_id"] == "forest_edge"
    assert result["physics_diff"]["mp_delta"] == 0
    assert "moved_recently" in result["physics_diff"]["state_flags_add"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "forest_edge"
    assert player["mp"] == 50
    assert "moved_recently" in json.loads(player["state_flags_json"] or "[]")


def test_story_pack_locked_exit_returns_invalid_without_state_flag(tmp_path):
    """
    功能：验证真实赤灯剧本包出口 conditions 未满足时，移动会在校验层失败且不写位置。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示剧本包线索门控或 UI 行动禁用模型回归。
    """
    loop = build_loop(tmp_path)
    registry = StoryPackRegistry(str(Path(__file__).resolve().parents[2] / "story_packs"))
    registry.refresh()
    loop.story_pack_registry = registry
    loop.db_updater.apply_diff("player_01", {"location_id": "red_lantern_lane"})
    context = TurnRequestContext(
        trace_id="t-red-lantern-lock-001",
        request_id="r-red-lantern-lock-001",
        session_id="s-red-lantern-lock-001",
        pack_id="echoes_under_red_lantern",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )

    result = asyncio.run(loop.run("前往静默钟院", request_context=context))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "invalid"
    assert "现在还不能走向静默钟院" in "；".join(result["validation_errors"])
    assert result["physics_diff"] is None
    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "red_lantern_lane"
    snapshot = result["scene_snapshot"]
    assert snapshot is not None
    locked_affordances = [
        item for item in snapshot["affordances"] if item.get("location_id") == "bell_courtyard"
    ]
    assert locked_affordances
    assert locked_affordances[0]["enabled"] is False
    assert locked_affordances[0]["user_input"] == "走向静默钟院"
    assert locked_affordances[0]["reason"] == "需要先完成前置线索，才能走向静默钟院。"
    assert "静默钟院" not in "；".join(result["quick_actions"])


def test_story_pack_conditioned_exit_moves_after_required_flag(tmp_path):
    """
    功能：验证真实赤灯剧本包出口 conditions 满足后，同一移动输入可正常切换 pack 场景。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示条件解锁、位置写入或移动资源结算回归。
    """
    loop = build_loop(tmp_path)
    registry = StoryPackRegistry(str(Path(__file__).resolve().parents[2] / "story_packs"))
    registry.refresh()
    loop.story_pack_registry = registry
    loop.db_updater.apply_diff(
        "player_01",
        {
            "location_id": "red_lantern_lane",
            "state_flags_add": ["ledger_second_boat_found"],
        },
    )
    context = TurnRequestContext(
        trace_id="t-red-lantern-lock-002",
        request_id="r-red-lantern-lock-002",
        session_id="s-red-lantern-lock-002",
        pack_id="echoes_under_red_lantern",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )

    result = asyncio.run(loop.run("前往静默钟院", request_context=context))

    assert result["is_valid"] is True
    assert result["physics_diff"]["location_id"] == "bell_courtyard"
    assert result["physics_diff"]["mp_delta"] == 0
    snapshot = result["scene_snapshot"]
    assert snapshot is not None
    assert snapshot["current_location"]["id"] == "bell_courtyard"
    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "bell_courtyard"


def test_red_lantern_scribe_talk_fires_clue_trigger(tmp_path):
    """
    功能：验证旧账房燕书吏交谈是完整剧本交互，会触发证词叙事与线索状态。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示赤灯剧本包中 UI 可见交谈按钮退化为无内容通用交谈。
    """
    loop = build_loop(tmp_path)
    registry = StoryPackRegistry(str(Path(__file__).resolve().parents[2] / "story_packs"))
    registry.refresh()
    loop.story_pack_registry = registry
    loop.db_updater.apply_diff("player_01", {"location_id": "ledgers_room"})
    context = TurnRequestContext(
        trace_id="t-red-lantern-scribe-001",
        request_id="r-red-lantern-scribe-001",
        session_id="s-red-lantern-scribe-001",
        pack_id="echoes_under_red_lantern",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )

    result = asyncio.run(loop.run("询问账房燕书吏", request_context=context))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "talk"
    assert result["physics_diff"]["mp_delta"] == 0
    assert "conversation_started" in result["physics_diff"]["state_flags_add"]
    assert "scribe_yan_missing_page" in result["physics_diff"]["state_flags_add"]
    assert any(event.get("trigger_id") == "talk_scribe_yan" for event in result["trigger_events"])
    assert "账册少了一页" in result["final_response"]
    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    player_flags = json.loads(player["state_flags_json"] or "[]")
    assert "scribe_yan_missing_page" in player_flags


def test_main_event_loop_talk_updates_flag_without_mp_cost(tmp_path):
    """
    功能：验证普通交谈只更新交谈标记，不再默认消耗法力。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("和地精说话"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "talk"
    assert result["action_intent"]["target_id"] == "goblin_01"
    assert result["physics_diff"]["mp_delta"] == 0
    assert "conversation_started" in result["physics_diff"]["state_flags_add"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["mp"] == 50
    assert "conversation_started" in json.loads(player["state_flags_json"] or "[]")


def test_main_event_loop_talk_without_target_returns_clarification(tmp_path):
    """
    功能：验证 main event loop talk without target returns clarification 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("我想和他说话"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    assert "你想交谈谁？当前没有明确可见目标。" == result["clarification_question"]
    assert result["should_advance_turn"] is False
    assert result["physics_diff"] is None
    assert result["turn_id"] == 0


def test_main_event_loop_clarification_builders_use_fallbacks_without_scene(tmp_path):
    """
    功能：验证移动/目标澄清在缺少 scene_snapshot 时使用确定性兜底文本。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 clarification 边界文案回归。
    """
    loop = build_loop(tmp_path)

    move_question = loop._build_move_clarification({})  # noqa: SLF001
    target_question = loop._build_target_clarification({}, "attack")  # noqa: SLF001

    assert move_question == "这里暂时没有明确出口，你想先观察周围吗？"
    assert target_question == "你想和谁攻击？当前没有明确可见目标。"


def test_main_event_loop_clarifier_failure_uses_fallback_question(tmp_path):
    """
    功能：验证 Clarifier 异常时主循环会使用确定性兜底问题，避免澄清回合失败。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 `_clarify_with_agent` 降级路径回归。
    """
    loop = build_loop(tmp_path)

    def _raise_clarifier_error(_envelope):
        """
        功能：提供 raise clarifier error 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("clarifier unavailable")

    loop.clarifier_agent.clarify = _raise_clarifier_error
    result = asyncio.run(loop.run("天气真不错"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    expected_question = "我还没有理解你的行动，你想观察、移动、交谈，还是休息？"
    assert result["clarification_question"] == expected_question
    assert result["should_advance_turn"] is False


def test_main_event_loop_interact_updates_flag_without_mp_cost(tmp_path):
    """
    功能：验证普通观察/交互只更新状态标记，不再默认消耗法力。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "observe"
    assert "observed_surroundings" in result["physics_diff"]["state_flags_add"]
    assert result["scene_snapshot"]["current_location"]["id"] == "village_square"
    assert result["scene_snapshot"]["exits"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert "observed_surroundings" in json.loads(player["state_flags_json"] or "[]")


def test_main_event_loop_wait_accepts_sit_naturally(tmp_path):
    """
    功能：验证 main event loop wait accepts sit naturally 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("我坐一会"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "wait"
    assert "waited_recently" in result["physics_diff"]["state_flags_add"]
    assert result["turn_id"] == 1


def test_main_event_loop_rest_recovers_resources_deterministically(tmp_path):
    """
    功能：验证 main event loop rest recovers resources deterministically 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    loop.db_updater.apply_diff("player_01", {"hp_delta": -10, "mp_delta": -10})

    result = asyncio.run(loop.run("我休息一下"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "rest"
    assert result["physics_diff"]["hp_delta"] == 1
    assert result["physics_diff"]["mp_delta"] == 2

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["hp"] == 91
    assert player["mp"] == 42


def test_main_event_loop_continue_move_uses_scene_exit(tmp_path):
    """
    功能：验证 main event loop continue move uses scene exit 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("前往森林"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "move"
    assert result["action_intent"]["parameters"]["location_id"] == "forest_edge"
    assert result["physics_diff"]["location_id"] == "forest_edge"

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "forest_edge"


def test_main_event_loop_commit_sandbox_merges_shadow_state(tmp_path):
    """
    功能：验证 main event loop commit sandbox merges shadow state 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    sandbox_turn = asyncio.run(loop.run("前往森林", is_sandbox_mode=True))
    assert sandbox_turn["is_valid"] is True

    active_player_before = loop.entity_probes.get_character_stats("player_01")
    shadow_player_before = loop.entity_probes.get_character_stats("player_01", use_shadow=True)
    assert active_player_before is not None
    assert shadow_player_before is not None
    assert active_player_before["current_location_id"] == "village_square"
    assert shadow_player_before["current_location_id"] == "forest_edge"

    merged = asyncio.run(loop.run("并入主线", is_sandbox_mode=True))
    assert merged["is_valid"] is True
    assert merged["is_sandbox_mode"] is False

    active_player_after = loop.entity_probes.get_character_stats("player_01")
    assert active_player_after is not None
    assert active_player_after["current_location_id"] == "forest_edge"
    assert loop.db_updater.has_shadow_state() is False


def test_main_event_loop_discard_sandbox_rolls_back_shadow_state(tmp_path):
    """
    功能：验证 main event loop discard sandbox rolls back shadow state 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    sandbox_turn = asyncio.run(loop.run("前往森林", is_sandbox_mode=True))
    assert sandbox_turn["is_valid"] is True
    assert loop.db_updater.has_shadow_state() is True

    discarded = asyncio.run(loop.run("回滚沙盒", is_sandbox_mode=True))
    assert discarded["is_valid"] is True
    assert discarded["is_sandbox_mode"] is False
    assert loop.db_updater.has_shadow_state() is False

    active_player = loop.entity_probes.get_character_stats("player_01")
    assert active_player is not None
    assert active_player["current_location_id"] == "village_square"


def test_main_event_loop_sandbox_control_fails_outside_sandbox_mode(tmp_path):
    """
    功能：验证 main event loop sandbox control fails outside sandbox mode 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("并入主线"))

    assert result["is_valid"] is False
    assert "当前不在沙盒模式" in "；".join(result["validation_errors"])


def test_main_event_loop_use_item_consumes_inventory_and_applies_effect(tmp_path):
    """
    功能：验证 main event loop use item consumes inventory and applies effect 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    loop.db_updater.apply_diff("player_01", {"hp_delta": -40})

    result = asyncio.run(loop.run("使用药水"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "use_item"
    assert result["physics_diff"]["hp_delta"] == 20

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["hp"] == 80
    assert loop.entity_probes.get_inventory_item("player_01", "health_potion_01") is None


def test_main_event_loop_use_item_fails_when_inventory_missing_item(tmp_path):
    """
    功能：验证 main event loop use item fails when inventory missing item 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)
    loop.db_updater.consume_item("player_01", "health_potion_01")

    result = asyncio.run(loop.run("使用药水"))

    assert result["is_valid"] is False
    assert "背包中不存在该物品" in result["validation_errors"]
    assert result["final_response"]


def test_main_event_loop_config_and_write_plan_edge_branches(tmp_path):
    """
    功能：验证配置动作过滤非法 flag、整数转换降级、写计划消费物品/目标伤害与未知写操作分支。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示确定性结算或写计划边界回归。
    """
    loop = build_loop(tmp_path)
    loop.rules.setdefault("resolution", {})["inspect"] = {
        "hp_delta": 0,
        "mp_delta": 0,
        "state_flags_add": ["valid_flag", 123, None],
    }

    configured = loop._resolve_configured_action("inspect", {"base": True})  # noqa: SLF001
    write_plan = loop._build_write_plan(  # noqa: SLF001
        {
            "active_character_id": "player_01",
            "action_intent": {"type": "attack", "target_id": "goblin_01"},
            "physics_diff": {"consumed_item_id": "potion", "target_hp_delta": -3},
            "is_sandbox_mode": False,
        }
    )

    assert loop._to_int("bad", default=7) == 7  # noqa: SLF001
    assert configured["state_flags_add"] == ["valid_flag"]
    assert {
        "type": "consume_item",
        "owner_id": "player_01",
        "item_id": "potion",
        "use_shadow": False,
    } in write_plan
    assert {
        "type": "apply_diff",
        "entity_id": "goblin_01",
        "diff": {"hp_delta": -3},
        "use_shadow": False,
    } in write_plan
    assert loop._execute_write_op({"type": "unknown"}) is False  # noqa: SLF001


def test_main_event_loop_returns_controlled_failure_for_missing_character(tmp_path):
    """
    功能：验证 main event loop returns controlled failure for missing character 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("攻击地精", initial_character_id="missing_player"))

    assert result["is_valid"] is False
    assert "当前角色不存在" in "；".join(result["validation_errors"])
    assert result["action_intent"] is None
    assert result["final_response"]


def test_main_event_loop_background_emit_overflow_enqueues_outbox_events(tmp_path):
    """
    功能：验证后台外环投递达到上限时会降级写入 outbox，且只写入可重放事件类型。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示后台溢出降级路径回归。
    """
    loop = build_loop(tmp_path)
    loop.outer_max_pending_tasks = 0
    state = {
        "is_valid": True,
        "physics_diff": {"hp_delta": 1},
        "active_character_id": "player_01",
        "is_sandbox_mode": False,
        "turn_id": 7,
        "user_input": "观察周围",
        "final_response": "叙事",
        "active_character": {"location": "unknown"},
    }

    result = loop._emit_outer_events_background(state)  # noqa: SLF001
    pending = loop.db_updater.list_pending_outer_events(limit=10)
    event_names = {str(item["event_name"]) for item in pending}
    assert result["status"] == "failed"
    assert result["detail"]["queued_to_outbox"] is True
    assert event_names == {"state_changed", "turn_ended", "world_evolution"}


def test_main_event_loop_background_emit_starts_async_task(tmp_path):
    """
    功能：验证后台外环投递在容量充足时会创建任务并返回 started 状态。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示后台任务调度分支回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)
    state = {
        "is_valid": True,
        "physics_diff": {"hp_delta": 1},
        "active_character_id": "player_01",
        "is_sandbox_mode": False,
        "turn_id": 8,
        "user_input": "观察周围",
        "final_response": "叙事",
        "should_advance_turn": True,
        "active_character": {"location": "unknown"},
    }

    async def _run_once() -> dict[str, object]:
        """
        功能：提供 run once 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        result = loop._emit_outer_events_background(state)  # noqa: SLF001
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(_run_once())
    assert result["status"] == "started"
    assert result["detail"]["mode"] == "workflow_background"


def test_main_event_loop_replay_outbox_unsupported_event_logs_failure(tmp_path, caplog):
    """
    功能：验证补偿重放遇到不支持事件类型时会回写失败状态并输出告警日志。
    入参：tmp_path（pytest fixture）；caplog（日志捕获器）。
    出参：None。
    异常：断言失败表示 outbox 失败回写路径不可观测。
    """
    loop = build_loop(tmp_path)
    loop.db_updater.enqueue_outer_event("unsupported_event", {"foo": "bar"}, "seed")

    async def _replay_once() -> None:
        """
        功能：提供 replay once 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        await loop._replay_outbox_once()  # noqa: SLF001

    caplog.set_level("WARNING", logger="Workflow.MainLoop")
    asyncio.run(_replay_once())
    assert "外环补偿重放失败[event=unsupported_event" in caplog.text


def test_main_event_loop_replay_outbox_delivers_supported_events(tmp_path):
    """
    功能：验证补偿重放能投递三类支持事件，并标记 delivered。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 outbox 成功重放或 delivered 标记回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)
    loop.db_updater.enqueue_outer_event(
        "state_changed",
        {"entity_id": "player_01", "diff": {"hp_delta": 1}, "is_sandbox": False},
        "seed",
    )
    loop.db_updater.enqueue_outer_event(
        "turn_ended",
        {"turn_id": 1, "user_input": "观察", "final_response": "叙事"},
        "seed",
    )
    loop.db_updater.enqueue_outer_event(
        "world_evolution",
        {"time_passed_minutes": 10, "location_id": "unknown"},
        "seed",
    )

    asyncio.run(loop._replay_outbox_once())  # noqa: SLF001

    assert len(outer.state_events) == 1
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    assert loop.db_updater.list_pending_outer_events(limit=10) == []


def test_main_event_loop_schedule_outbox_replay_respects_interval_and_active_task(tmp_path):
    """
    功能：验证 outbox 调度会遵守间隔和进行中任务限制，避免重复创建补偿任务。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 outbox 调度节流回归。
    """
    loop = build_loop(tmp_path)

    async def _schedule_twice() -> None:
        """
        功能：提供 schedule twice 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        loop._schedule_outbox_replay()  # noqa: SLF001
        first_task = loop._outer_replay_task
        loop._schedule_outbox_replay()  # noqa: SLF001
        assert loop._outer_replay_task is first_task
        assert first_task is not None
        await first_task

    asyncio.run(_schedule_twice())
