import asyncio
import sqlite3

from game_workflows.async_watchers import GlobalEventWorkflow, WorkflowOuterLoopBridge
from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent
from tools.sqlite_db.db_updater import DBUpdater


def test_workflow_outer_loop_bridge_turn_event_runs():
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
    db_path = tmp_path / "tre_state.db"
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
    db_path = tmp_path / "tre_state.db"
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
    db_path = tmp_path / "tre_state.db"
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


def test_workflow_outer_loop_bridge_world_evolution_updates_world_state(tmp_path):
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
