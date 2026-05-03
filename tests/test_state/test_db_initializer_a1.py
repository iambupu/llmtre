from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
import yaml

from state.tools import db_initializer
from state.tools.db_initializer import DBInitializer


def _write_json(path: Path, payload: object) -> None:
    """
    功能：以 UTF-8 写入 JSON 测试数据。
    入参：path（Path）：目标路径；payload（object）：可序列化数据。
    出参：None。
    异常：文件写入或 JSON 序列化失败时向上抛出。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _item_payload(item_id: str, name: str) -> dict[str, object]:
    """
    功能：构造满足 ItemTemplate 的最小物品数据。
    入参：item_id（str）：物品 ID；name（str）：物品名。
    出参：dict[str, object]，物品 JSON。
    异常：无。
    """
    return {
        "item_id": item_id,
        "name": name,
        "description": f"{name} 描述",
        "item_type": "consumable",
        "requirements": {},
        "effects": [{"target_attribute": "hp", "value": 1}],
        "is_stackable": True,
    }


def _entity_payload(default_inventory: list[str]) -> dict[str, object]:
    """
    功能：构造满足 EntityTemplate 的最小实体数据。
    入参：default_inventory（list[str]）：默认背包物品 ID 列表。
    出参：dict[str, object]，实体 JSON。
    异常：无。
    """
    return {
        "entity_id": "player_01",
        "name": "玩家",
        "entity_type": "player",
        "description": "测试玩家",
        "base_stats": {
            "strength": 10,
            "agility": 10,
            "intelligence": 10,
            "constitution": 10,
        },
        "resources": {"hp": 10, "max_hp": 10, "mp": 5, "max_mp": 5},
        "default_inventory": default_inventory,
    }


def _patch_initializer_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    """
    功能：隔离 DBInitializer 的 DATA/MODS/REGISTRY 全局路径。
    入参：tmp_path；monkeypatch。
    出参：tuple[Path, Path, Path]，数据目录、MOD 目录、注册表路径。
    异常：目录创建失败时向上抛出。
    """
    data_dir = tmp_path / "state" / "data"
    mods_dir = tmp_path / "mods"
    registry_path = tmp_path / "config" / "mod_registry.yml"
    data_dir.mkdir(parents=True)
    mods_dir.mkdir()
    registry_path.parent.mkdir()
    monkeypatch.setattr(db_initializer, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db_initializer, "MODS_DIR", str(mods_dir))
    monkeypatch.setattr(db_initializer, "REGISTRY_PATH", str(registry_path))
    return data_dir, mods_dir, registry_path


def test_load_mod_registry_missing_and_broken_file_logs_and_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """
    功能：验证 MOD 注册表缺失或损坏时降级为空列表，并记录日志证据。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示注册表降级策略回归。
    """
    _, _, registry_path = _patch_initializer_paths(tmp_path, monkeypatch)
    registry_path.unlink(missing_ok=True)

    with caplog.at_level(logging.WARNING, logger="DBInitializer"):
        initializer = DBInitializer(str(tmp_path / "state" / "core_data" / "test.db"))
    assert initializer.mod_registry == []
    assert "未找到 mod_registry.yml" in caplog.text

    registry_path.write_text("not: [valid", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="DBInitializer"):
        broken = DBInitializer(str(tmp_path / "state" / "core_data" / "broken.db"))
    assert broken.mod_registry == []
    assert "加载 MOD 注册表失败" in caplog.text


def test_load_mod_registry_filters_disabled_and_sorts_by_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证仅启用 MOD 会进入注册表，并按 priority 从低到高排序。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示 MOD 优先级排序回归。
    """
    _, _, registry_path = _patch_initializer_paths(tmp_path, monkeypatch)
    registry_path.write_text(
        yaml.safe_dump(
            {
                "active_mods": [
                    {"mod_id": "high", "enabled": True, "priority": 90},
                    {"mod_id": "disabled", "enabled": False, "priority": 1},
                    {"mod_id": "low", "enabled": True, "priority": 10},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    initializer = DBInitializer(str(tmp_path / "state" / "core_data" / "sorted.db"))

    assert [mod["mod_id"] for mod in initializer.mod_registry] == ["low", "high"]


def test_initialize_db_merges_mods_rejects_bad_data_and_imports_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """
    功能：验证数据库初始化会合并本体与 MOD 数据，覆盖 strict_override、坏数据拒绝和默认背包。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 MOD 数据导入或实体默认背包写入回归。
    """
    data_dir, mods_dir, registry_path = _patch_initializer_paths(tmp_path, monkeypatch)
    _write_json(data_dir / "items.json", [_item_payload("item_01", "Base Potion")])
    _write_json(data_dir / "entities.json", [_entity_payload(["item_01", "item_01"])])

    smart_dir = mods_dir / "smart_mod" / "data"
    _write_json(
        smart_dir / "items.json",
        [
            {
                "item_id": "item_01",
                "name": "Merged Potion",
                "effects": [{"target_attribute": "hp", "value": 5}],
                "description": "不在白名单中，不应覆盖",
            },
            {"item_id": "bad_item", "description": "缺少必填字段"},
        ],
    )
    _write_json(
        smart_dir / "entities.json",
        [{"entity_id": "player_01", "default_inventory": ["item_02"]}],
    )

    strict_dir = mods_dir / "strict_mod" / "data"
    _write_json(strict_dir / "items.json", [_item_payload("item_01", "Strict Potion")])

    registry_path.write_text(
        yaml.safe_dump(
            {
                "active_mods": [
                    {
                        "mod_id": "strict_mod",
                        "enabled": True,
                        "priority": 20,
                        "conflict_strategy": "strict_override",
                    },
                    {
                        "mod_id": "smart_mod",
                        "enabled": True,
                        "priority": 10,
                        "conflict_strategy": "smart_merge",
                        "allowed_fields": ["name", "effects", "default_inventory"],
                    },
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "state" / "core_data" / "merged.db"

    with caplog.at_level(logging.INFO, logger="DBInitializer"):
        DBInitializer(str(db_path)).initialize_db()

    with sqlite3.connect(db_path) as conn:
        item_row = conn.execute(
            "SELECT name, description, effects_json FROM items WHERE item_id = ?",
            ("item_01",),
        ).fetchone()
        bad_item = conn.execute("SELECT 1 FROM items WHERE item_id = 'bad_item'").fetchone()
        inventory_rows = dict(
            conn.execute(
                """
                SELECT item_id, quantity
                FROM inventory_active
                WHERE owner_id = 'player_01'
                """
            ).fetchall()
        )
        entity_row = conn.execute(
            "SELECT name, hp, max_hp FROM entities_active WHERE entity_id = 'player_01'"
        ).fetchone()

    assert item_row is not None
    assert item_row[0] == "Strict Potion"
    assert "Strict Potion 描述" == item_row[1]
    assert json.loads(item_row[2])[0]["value"] == 1
    assert bad_item is None
    assert inventory_rows == {"item_01": 2, "item_02": 1}
    assert entity_row == ("玩家", 10, 10)
    assert "数据 [bad_item] 合并后违反契约，已被拒绝加载" in caplog.text
    assert "数据库初始化及 MOD 数据合并成功" in caplog.text
