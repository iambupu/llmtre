from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tools.entity.entity_probes import EntityProbes


def _init_probe_db(db_path: Path) -> None:
    """
    功能：初始化 EntityProbes 测试所需最小 SQLite 表与数据。
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
                    name TEXT,
                    entity_type TEXT,
                    hp INTEGER,
                    current_location_id TEXT
                )
                """
            )
        cursor.execute(
            """
            CREATE TABLE inventory_active (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE inventory_shadow (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE items (
                item_id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                item_type TEXT,
                effects_json TEXT,
                hooks_json TEXT,
                is_stackable INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE world_state_active (
                key TEXT PRIMARY KEY,
                value_json TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE world_state_shadow (
                key TEXT PRIMARY KEY,
                value_json TEXT
            )
            """
        )
        cursor.execute(
            "INSERT INTO entities_active VALUES (?, ?, ?, ?, ?)",
            ("player_01", "玩家", "player", 10, "road"),
        )
        cursor.execute(
            "INSERT INTO entities_shadow VALUES (?, ?, ?, ?, ?)",
            ("player_01", "影子玩家", "player", 8, "shadow_road"),
        )
        cursor.execute(
            "INSERT INTO entities_active VALUES (?, ?, ?, ?, ?)",
            ("npc_01", "守卫", "npc", 5, "road"),
        )
        cursor.execute(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("potion", "药水", "恢复生命", "consumable", '[{"value": 5}]', '{"on_use": "heal"}', 1),
        )
        cursor.execute(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("broken", "坏物品", "坏 JSON", "quest", "{bad", "{}", 0),
        )
        cursor.execute("INSERT INTO inventory_active VALUES (?, ?, ?)", ("player_01", "potion", 2))
        cursor.execute(
            "INSERT INTO world_state_active VALUES (?, ?)",
            ("loc_road", '{"name": "道路"}'),
        )
        cursor.execute(
            "INSERT INTO world_state_active VALUES (?, ?)",
            ("loc_bad_json", "{bad"),
        )
        cursor.execute(
            "INSERT INTO world_state_active VALUES (?, ?)",
            ("loc_not_dict", "[1, 2]"),
        )
        conn.commit()


def test_entity_probes_read_stats_inventory_item_location_and_nearby(tmp_path: Path) -> None:
    """
    功能：验证 EntityProbes 正常读取角色、背包、物品定义、地点与附近实体。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示只读探针主路径回归。
    """
    db_path = tmp_path / "probes.db"
    _init_probe_db(db_path)
    probes = EntityProbes(str(db_path))

    active_stats = probes.get_character_stats("player_01")
    shadow_stats = probes.get_character_stats("player_01", use_shadow=True)
    inventory = probes.check_inventory("player_01")
    item = probes.get_inventory_item("player_01", "potion")
    item_definition = probes.get_item_definition("potion")
    location = probes.get_location_info("road")
    nearby = probes.list_nearby_entities("road")

    assert active_stats is not None
    assert active_stats["hp"] == 10
    assert shadow_stats is not None
    assert shadow_stats["name"] == "影子玩家"
    assert inventory == [
        {
            "item_id": "potion",
            "quantity": 2,
            "name": "药水",
            "description": "恢复生命",
            "item_type": "consumable",
        }
    ]
    assert item == inventory[0]
    assert item_definition is not None
    assert item_definition["effects"] == [{"value": 5}]
    assert item_definition["hooks"] == {"on_use": "heal"}
    assert location == {"name": "道路"}
    assert {row["entity_id"] for row in nearby} == {"player_01", "npc_01"}


def test_entity_probes_missing_rows_and_empty_inventory_return_none_or_empty(
    tmp_path: Path,
) -> None:
    """
    功能：验证实体/物品/地点缺失和空背包时返回 None 或空列表。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示缺失数据降级回归。
    """
    db_path = tmp_path / "probes_missing.db"
    _init_probe_db(db_path)
    probes = EntityProbes(str(db_path))

    assert probes.get_character_stats("missing") is None
    assert probes.check_inventory("missing") == []
    assert probes.get_inventory_item("player_01", "missing") is None
    assert probes.get_item_definition("missing") is None
    assert probes.get_location_info("missing") is None
    assert probes.get_location_info("not_dict") is None
    assert probes.list_nearby_entities("missing") == []


def test_entity_probes_bad_json_and_missing_tables_log_and_downgrade(
    tmp_path: Path,
    caplog,
) -> None:
    """
    功能：验证坏 JSON 与缺表 SQLite 异常会记录日志并降级返回空值。
    入参：tmp_path；caplog。
    出参：None。
    异常：断言失败表示探针异常降级或日志证据回归。
    """
    db_path = tmp_path / "probes_bad.db"
    _init_probe_db(db_path)
    probes = EntityProbes(str(db_path))

    with caplog.at_level(logging.WARNING, logger="EntityProbes"):
        assert probes.get_item_definition("broken") is None
        assert probes.get_location_info("bad_json") is None

    assert "物品定义探针读取失败" in caplog.text
    assert "地点探针读取失败" in caplog.text

    broken_db = tmp_path / "missing_tables.db"
    with caplog.at_level(logging.WARNING, logger="EntityProbes"):
        broken = EntityProbes(str(broken_db))
        assert broken.get_character_stats("player_01") is None
        assert broken.check_inventory("player_01") == []
        assert broken.get_item_definition("potion") is None
        assert broken.get_location_info("road") is None
        assert broken.list_nearby_entities("road") == []

    assert "角色数值探针读取失败" in caplog.text
    assert "背包探针读取失败" in caplog.text
    assert "附近实体探针读取失败" in caplog.text
