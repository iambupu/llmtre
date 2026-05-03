from __future__ import annotations

import sqlite3
from pathlib import Path

from state.models.action import ActionEffect, ActionTemplate, ActionType, EffectType
from state.tools.state_mutators import StateMutators


def _init_state_mutator_db(db_path: Path) -> None:
    """
    功能：初始化 StateMutators 测试所需的最小 Active/Shadow 表结构与数据。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for table in ("entities_active", "entities_shadow"):
            cursor.execute(
                f"""
                CREATE TABLE {table} (
                    entity_id TEXT PRIMARY KEY,
                    hp INTEGER NOT NULL,
                    max_hp INTEGER NOT NULL,
                    mp INTEGER NOT NULL,
                    max_mp INTEGER NOT NULL
                )
                """
            )
        for table in ("inventory_active", "inventory_shadow"):
            cursor.execute(
                f"""
                CREATE TABLE {table} (
                    owner_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    UNIQUE(owner_id, item_id)
                )
                """
            )
        cursor.execute(
            """
            INSERT INTO entities_active(entity_id, hp, max_hp, mp, max_mp)
            VALUES('player_01', 5, 10, 2, 5)
            """
        )
        cursor.execute(
            """
            INSERT INTO entities_shadow(entity_id, hp, max_hp, mp, max_mp)
            VALUES('player_01', 8, 10, 4, 5)
            """
        )
        cursor.execute(
            """
            INSERT INTO inventory_active(owner_id, item_id, quantity)
            VALUES('player_01', 'potion', 3)
            """
        )
        cursor.execute(
            """
            INSERT INTO inventory_active(owner_id, item_id, quantity)
            VALUES('npc_01', 'potion', 1)
            """
        )
        conn.commit()


def _action(*effects: ActionEffect) -> ActionTemplate:
    """
    功能：构造最小 ActionTemplate，减少测试中重复字段。
    入参：effects（ActionEffect）：成功效果列表。
    出参：ActionTemplate。
    异常：模型校验失败时抛出 pydantic.ValidationError。
    """
    return ActionTemplate(
        action_id="act_test",
        name="测试动作",
        action_type=ActionType.INTERACT,
        trigger_description="测试",
        success_effects=list(effects),
    )


def _read_entity(db_path: Path, table: str) -> tuple[int, int]:
    """
    功能：读取指定实体表中 player_01 的 hp/mp。
    入参：db_path（Path）：SQLite 文件路径；table（str）：实体表名。
    出参：tuple[int, int]，hp 与 mp。
    异常：查询失败或数据缺失时抛出 AssertionError/sqlite3.Error。
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(f"SELECT hp, mp FROM {table} WHERE entity_id = 'player_01'").fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def test_modify_hp_uses_active_or_shadow_and_clamps_bounds(tmp_path: Path) -> None:
    """
    功能：验证 modify_hp 会按 Active/Shadow 表写入，并按 max_hp/0 钳制边界。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示直接生命值修改分支回归。
    """
    db_path = tmp_path / "mutators.db"
    _init_state_mutator_db(db_path)
    mutators = StateMutators(str(db_path))

    assert mutators.modify_hp("player_01", 99) is True
    assert _read_entity(db_path, "entities_active")[0] == 10
    assert _read_entity(db_path, "entities_shadow")[0] == 8

    assert mutators.modify_hp("player_01", -99, use_shadow=True) is True
    assert _read_entity(db_path, "entities_shadow")[0] == 0
    assert mutators.modify_hp("missing", 1) is False


def test_apply_action_resource_change_updates_shadow_only(tmp_path: Path) -> None:
    """
    功能：验证 RESOURCE_CHANGE 效果在 use_shadow=True 时只修改影子表资源。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示动作效果表选择或资源钳制回归。
    """
    db_path = tmp_path / "mutators_resource.db"
    _init_state_mutator_db(db_path)
    mutators = StateMutators(str(db_path))
    effect = ActionEffect(
        effect_type=EffectType.RESOURCE_CHANGE,
        target_id="player_01",
        parameters={"attribute": "mp", "value": 99},
    )

    assert mutators.apply_action(_action(effect), use_shadow=True) is True

    assert _read_entity(db_path, "entities_active") == (5, 2)
    assert _read_entity(db_path, "entities_shadow") == (8, 5)


def test_apply_action_item_transfer_deducts_and_upserts_inventory(tmp_path: Path) -> None:
    """
    功能：验证 ITEM_TRANSFER 会扣减来源并对目标库存执行 upsert 增量。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示物品转移 SQL 分支回归。
    """
    db_path = tmp_path / "mutators_inventory.db"
    _init_state_mutator_db(db_path)
    mutators = StateMutators(str(db_path))
    effect = ActionEffect(
        effect_type=EffectType.ITEM_TRANSFER,
        target_id="player_01",
        parameters={
            "from_id": "player_01",
            "to_id": "npc_01",
            "item_id": "potion",
            "quantity": 2,
        },
    )

    assert mutators.apply_action(_action(effect)) is True

    with sqlite3.connect(db_path) as conn:
        rows = dict(
            conn.execute(
                """
                SELECT owner_id, quantity
                FROM inventory_active
                WHERE item_id = 'potion'
                """
            ).fetchall()
        )
    assert rows == {"player_01": 1, "npc_01": 3}


def test_apply_action_rolls_back_when_later_effect_fails(tmp_path: Path, capsys) -> None:
    """
    功能：验证同一动作内后续效果失败时会回滚前序成功写入并返回 False。
    入参：tmp_path；capsys。
    出参：None。
    异常：断言失败表示事务边界或失败输出回归。
    """
    db_path = tmp_path / "mutators_rollback.db"
    _init_state_mutator_db(db_path)
    mutators = StateMutators(str(db_path))
    valid_effect = ActionEffect(
        effect_type=EffectType.RESOURCE_CHANGE,
        target_id="player_01",
        parameters={"attribute": "hp", "value": 3},
    )
    invalid_effect = ActionEffect(
        effect_type=EffectType.RESOURCE_CHANGE,
        target_id="player_01",
        parameters={"attribute": "unknown", "value": 1},
    )

    assert mutators.apply_action(_action(valid_effect, invalid_effect)) is False

    assert _read_entity(db_path, "entities_active") == (5, 2)
    assert "Action execution failed" in capsys.readouterr().out


def test_apply_action_stops_before_db_when_preconditions_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    功能：验证前置条件失败时不会打开数据库事务，也不会执行任何效果。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示前置条件短路回归。
    """
    db_path = tmp_path / "mutators_preconditions.db"
    _init_state_mutator_db(db_path)
    mutators = StateMutators(str(db_path))
    monkeypatch.setattr(mutators, "_verify_preconditions", lambda pre_conditions: False)
    monkeypatch.setattr(
        mutators,
        "_get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("不应连接数据库")),
    )
    effect = ActionEffect(
        effect_type=EffectType.RESOURCE_CHANGE,
        target_id="player_01",
        parameters={"attribute": "hp", "value": 1},
    )

    assert mutators.apply_action(_action(effect)) is False
