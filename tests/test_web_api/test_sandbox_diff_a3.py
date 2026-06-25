"""
功能：覆盖 A3 沙盒差异摘要生成器。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.sandbox_diff import build_sandbox_diff_summary


def _seed_diff_db(db_path: Path) -> None:
    """
    功能：创建最小 Active/Shadow 状态表并写入可比较差异。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        cursor.execute("""
            CREATE TABLE entities_active (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER,
                max_hp INTEGER,
                mp INTEGER,
                max_mp INTEGER,
                current_location_id TEXT,
                state_flags_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE entities_shadow (
                entity_id TEXT PRIMARY KEY,
                hp INTEGER,
                max_hp INTEGER,
                mp INTEGER,
                max_mp INTEGER,
                current_location_id TEXT,
                state_flags_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE inventory_active (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER,
                UNIQUE(owner_id, item_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE inventory_shadow (
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER,
                UNIQUE(owner_id, item_id)
            )
        """)
        cursor.execute(
            "INSERT INTO entities_active VALUES('player_01', 10, 10, 5, 5, 'town', '[]')"
        )
        cursor.execute(
            "INSERT INTO entities_shadow VALUES('player_01', 7, 10, 4, 5, 'dock', '[\"wet\"]')"
        )
        cursor.execute("INSERT INTO inventory_active VALUES('player_01', 'coin', 2)")
        cursor.execute("INSERT INTO inventory_shadow VALUES('player_01', 'coin', 5)")
        cursor.execute(
            "INSERT INTO world_state_active(key, value_json) "
            "VALUES('weather', '{\"state\":\"clear\"}')"
        )
        cursor.execute(
            "INSERT INTO world_state_shadow(key, value_json) "
            "VALUES('weather', '{\"state\":\"rain\"}')"
        )
        connection.commit()


def test_sandbox_diff_summary_groups_active_shadow_changes(tmp_path: Path) -> None:
    """
    功能：验证沙盒差异摘要按角色、背包、世界状态分组返回结构化变化。
    入参：tmp_path（Path）：pytest 临时目录。
    出参：None。
    异常：断言失败表示 Active/Shadow diff 生成器回归。
    """
    db_path = tmp_path / "sandbox_diff.db"
    _seed_diff_db(db_path)

    summary = build_sandbox_diff_summary(
        db_path=str(db_path),
        session_id="sess_a3diff01",
        trace_id="trc_a3diff01",
        mode="preview",
    )

    assert summary["has_changes"] is True
    assert summary["mode"] == "preview"
    assert any(change["field"] == "hp" for change in summary["character_changes"])
    assert summary["inventory_changes"][0]["field"] == "quantity"
    assert summary["world_changes"][0]["field"] == "value_json"
    assert summary["diagnostics"] == []
