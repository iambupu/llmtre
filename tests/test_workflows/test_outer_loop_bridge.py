"""
功能：覆盖 outer loop bridge 的回归测试。
"""

import asyncio
import logging
import sqlite3
from pathlib import Path

from game_workflows.async_watchers import GlobalEventWorkflow, WorkflowOuterLoopBridge
from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent
from tools.sqlite_db.db_updater import DBUpdater


def _seed_entity_table(db_path: Path) -> None:
    """
    功能：为外环成就奖励测试创建最小实体状态表。
    入参：db_path（Path）：测试数据库路径。
    出参：None。
    异常：sqlite 建表或写入失败时直接抛出，交由测试失败暴露。
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities_active (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER NOT NULL,
                max_hp INTEGER NOT NULL,
                mp INTEGER NOT NULL,
                max_mp INTEGER NOT NULL,
                current_location_id TEXT NOT NULL,
                state_flags_json TEXT NOT NULL DEFAULT '[]',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        conn.execute("""
            INSERT OR REPLACE INTO entities_active(
                entity_id, hp, max_hp, mp, max_mp, current_location_id, state_flags_json
            )
            VALUES ('player_01', 10, 10, 0, 5, 'ferry_landing', '[]')
            """)
        conn.commit()


def test_workflow_outer_loop_bridge_turn_event_runs():
    """
    功能：验证 workflow outer loop bridge turn event runs 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    bridge = WorkflowOuterLoopBridge()
    result = asyncio.run(
        bridge.emit_turn_ended(
            TurnEndedEvent(
                turn_id=1,
                user_input="观察周围",
                final_response="测试回合结束",
            )
        )
    )
    assert "audit completed" in str(result)


def test_workflow_outer_loop_bridge_state_event_can_trigger_achievement(tmp_path):
    """
    功能：验证 workflow outer loop bridge state event can trigger achievement 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    db_path = tmp_path / "tre_state.db"
    _seed_entity_table(db_path)
    bridge = WorkflowOuterLoopBridge(
        workflow=GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    )
    result = asyncio.run(
        bridge.emit_state_changed(
            StateChangedEvent(
                entity_id="player_01",
                diff={"target_hp_delta": -6},
                is_sandbox=False,
            )
        )
    )
    assert "first_blood" in str(result)


def test_workflow_outer_loop_bridge_achievement_is_deduplicated(tmp_path):
    """
    功能：验证 workflow outer loop bridge achievement is deduplicated 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    db_path = tmp_path / "tre_state.db"
    _seed_entity_table(db_path)
    workflow = GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    bridge = WorkflowOuterLoopBridge(workflow=workflow)
    first = asyncio.run(
        bridge.emit_state_changed(
            StateChangedEvent(
                entity_id="player_01",
                diff={"target_hp_delta": -6},
                is_sandbox=False,
            )
        )
    )
    second = asyncio.run(
        bridge.emit_state_changed(
            StateChangedEvent(
                entity_id="player_01",
                diff={"target_hp_delta": -3},
                is_sandbox=False,
            )
        )
    )

    assert "first_blood" in str(first)
    assert "first_blood" not in str(second)


def test_workflow_outer_loop_bridge_achievement_dedup_persists_with_db(tmp_path):
    """
    功能：验证 workflow outer loop bridge achievement dedup persists with db 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    db_path = tmp_path / "tre_state.db"
    _seed_entity_table(db_path)
    workflow_1 = GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    bridge_1 = WorkflowOuterLoopBridge(workflow=workflow_1)
    first = asyncio.run(
        bridge_1.emit_state_changed(
            StateChangedEvent(
                entity_id="player_01",
                diff={"target_hp_delta": -6},
                is_sandbox=False,
            )
        )
    )
    assert "first_blood" in str(first)

    workflow_2 = GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    bridge_2 = WorkflowOuterLoopBridge(workflow=workflow_2)
    second = asyncio.run(
        bridge_2.emit_state_changed(
            StateChangedEvent(
                entity_id="player_01",
                diff={"target_hp_delta": -2},
                is_sandbox=False,
            )
        )
    )
    assert "first_blood" not in str(second)

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute(
            """
            SELECT COUNT(1)
            FROM achievement_unlocks
            WHERE entity_id = ? AND achievement_id = ?
            """,
            ("player_01", "first_blood"),
        ).fetchone()[0]
    assert int(count) == 1


def test_workflow_outer_loop_reward_without_entity_table_does_not_mark_unlocked(tmp_path, caplog):
    """
    功能：验证外环成就奖励在缺少实体表时不会先标记成就解锁。
    入参：tmp_path；caplog。
    出参：None。
    异常：断言失败表示奖励失败后成就去重或补偿语义回归。
    """
    db_path = tmp_path / "tre_state.db"
    workflow = GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    bridge = WorkflowOuterLoopBridge(workflow=workflow)

    with caplog.at_level(logging.WARNING, logger="Workflow.AsyncWatchers"):
        result = asyncio.run(
            bridge.emit_state_changed(
                StateChangedEvent(
                    entity_id="player_01",
                    diff={"target_hp_delta": -6},
                    is_sandbox=False,
                )
            )
        )

    assert "first_blood" in str(result)
    assert "reward failed" in str(result)
    assert "外环成就奖励写入失败（未解锁）" in caplog.text
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute(
            """
            SELECT COUNT(1)
            FROM achievement_unlocks
            WHERE entity_id = ? AND achievement_id = ?
            """,
            ("player_01", "first_blood"),
        ).fetchone()[0]
    assert int(count) == 0
    assert workflow._mark_achievement_once("player_01", "first_blood") is True


def test_workflow_outer_loop_bridge_world_evolution_updates_world_state(tmp_path):
    """
    功能：验证 workflow outer loop bridge world evolution updates world state 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    db_path = tmp_path / "tre_state.db"
    workflow = GlobalEventWorkflow(timeout=60, verbose=False, db_path=str(db_path))
    bridge = WorkflowOuterLoopBridge(workflow=workflow)

    result = asyncio.run(
        bridge.emit_world_evolution(
            WorldEvolutionEvent(time_passed_minutes=15, location_id="forest_edge")
        )
    )
    assert "processed" in str(result)

    updater = DBUpdater(str(db_path))
    summary = updater.get_world_state("world.last_evolution_minutes")
    assert summary is not None
    assert summary["value"] == 15
