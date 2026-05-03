# ruff: noqa: E402,I001
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

# 直接执行 `python state/tools/db_initializer.py` 时，Python 只把 state/tools
# 放入 sys.path。这里显式补入仓库根目录，保证验收脚本与模块方式入口一致。
BASE_DIR = str(Path(__file__).resolve().parents[2])
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from state.models.entity import EntityTemplate  # noqa: E402

# 导入物理契约模型
from state.models.item import ItemTemplate  # noqa: E402
from state.tools.runtime_schema import ensure_runtime_tables  # noqa: E402

# 路径配置
DB_PATH = os.path.join(BASE_DIR, "state", "core_data", "tre_state.db")
DATA_DIR = os.path.join(BASE_DIR, "state", "data")
MODS_DIR = os.path.join(BASE_DIR, "mods")
REGISTRY_PATH = os.path.join(BASE_DIR, "config", "mod_registry.yml")

logger = logging.getLogger("DBInitializer")

def deep_merge(
    base: dict[str, Any],
    extension: dict[str, Any],
    allowed_fields: list[str] | None = None,
) -> dict[str, Any]:
    """
    功能：深度合并两个字典。
    入参：base；extension；allowed_fields。
    出参：Dict[str, Any]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    merged = base.copy()

    for key, value in extension.items():
        # 如果有白名单限制，且当前 key 不在白名单中，则跳过
        if allowed_fields and key not in allowed_fields:
            continue

        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
            # 列表追加（去重）
            for item in value:
                if item not in merged[key]:
                    merged[key].append(item)
        else:
            # 直接覆盖（标量）
            merged[key] = value

    return merged

class DBInitializer:
    """数据库初始化器：负责按优先级合并 MOD 数据并创建表结构"""

    def __init__(self, db_path: str = DB_PATH):
        """
        功能：初始化对象状态与依赖。
        入参：db_path。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.mod_registry = self._load_mod_registry()

    def _load_mod_registry(self) -> list[dict[str, Any]]:
        """
        功能：从 YAML 加载并按优先级排序已启用的 MOD。
        入参：无。
        出参：List[Dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not os.path.exists(REGISTRY_PATH):
            logger.warning("未找到 mod_registry.yml，将仅加载官方本体数据。")
            return []

        try:
            with open(REGISTRY_PATH, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                active_mods = config.get("active_mods", [])
                # 过滤启用的 MOD 并按优先级从低到高排序（低优先合并，高优后合并以实现覆盖）
                enabled_mods = [m for m in active_mods if m.get("enabled", True)]
                enabled_mods.sort(key=lambda x: x.get("priority", 50))
                return enabled_mods
        except Exception as e:
            logger.error(f"加载 MOD 注册表失败: {e}")
            return []

    def is_db_initialized(self) -> bool:
        """
        功能：检查数据库是否已具备可运行的核心 schema 与关键种子数据。
        入参：无。
        出参：bool，核心表齐全且存在关键种子时返回 True，否则返回 False。
        异常：函数内部捕获 sqlite/IO 异常并降级返回 False，同时记录告警日志。
        """
        if not os.path.exists(self.db_path):
            return False
        required_tables = (
            "entities_active",
            "items",
            "inventory_active",
            "timeline",
        )
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                for table_name in required_tables:
                    row = cursor.execute(
                        """
                        SELECT 1
                        FROM sqlite_master
                        WHERE type = 'table' AND name = ?
                        LIMIT 1
                        """,
                        (table_name,),
                    ).fetchone()
                    if row is None:
                        return False
                timeline_row = cursor.execute(
                    "SELECT 1 FROM timeline WHERE id = 0 LIMIT 1"
                ).fetchone()
                if timeline_row is None:
                    return False
                player_row = cursor.execute(
                    "SELECT 1 FROM entities_active WHERE entity_id = 'player_01' LIMIT 1"
                ).fetchone()
                if player_row is None:
                    return False
                item_row = cursor.execute("SELECT 1 FROM items LIMIT 1").fetchone()
                if item_row is None:
                    return False
                return True
        except Exception as error:  # noqa: BLE001
            logger.warning("数据库完整性检查失败，视为未初始化: %s", str(error))
            return False

    def initialize_db(self) -> None:
        """
        功能：执行完整的初始化流程。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info(f"正在初始化数据库: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            self._create_tables(cursor)
            self._import_merged_data(cursor)
            conn.commit()
            logger.info("数据库初始化及 MOD 数据合并成功。")
        except Exception as e:
            conn.rollback()
            logger.error(f"数据库初始化失败: {e}")
            raise
        finally:
            conn.close()

    def _create_tables(self, cursor: sqlite3.Cursor) -> None:
        """
        功能：创建核心表结构 (支持 Active 与 Shadow 双表)。
        入参：cursor。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # ... (保持之前的表结构创建代码，包含 Active/Shadow) ...
        entity_cols = """
            entity_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT,
            strength INTEGER,
            agility INTEGER,
            intelligence INTEGER,
            constitution INTEGER,
            hp INTEGER,
            max_hp INTEGER,
            mp INTEGER,
            max_mp INTEGER,
            traits_json TEXT,
            social_relations_json TEXT,
            current_location_id TEXT,
            behavior_pattern TEXT,
            state_flags_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        """
        cursor.execute(f"CREATE TABLE IF NOT EXISTS entities_active ({entity_cols})")
        cursor.execute(f"CREATE TABLE IF NOT EXISTS entities_shadow ({entity_cols})")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                item_type TEXT NOT NULL,
                min_strength INTEGER DEFAULT 0,
                min_agility INTEGER DEFAULT 0,
                min_intelligence INTEGER DEFAULT 0,
                effects_json TEXT,
                hooks_json TEXT,
                weight FLOAT DEFAULT 0.0,
                rarity TEXT DEFAULT 'common',
                usage_limit INTEGER DEFAULT -1,
                is_stackable BOOLEAN,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ... (此处省略其余 inventory, world_state, timeline 等表创建，逻辑同前) ...
        # [为了简洁，正式代码中应包含所有建表语句]
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_active (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER DEFAULT 1,
                UNIQUE(owner_id, item_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT,
                item_id TEXT,
                quantity INTEGER DEFAULT 1,
                UNIQUE(owner_id, item_id)
            )
            """
        )
        ensure_runtime_tables(cursor)

    def _import_merged_data(self, cursor: sqlite3.Cursor) -> None:
        """
        功能：核心合并导入逻辑：本体 -> MOD_1 -> MOD_2。
        入参：cursor。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """

        # 1. 合并物品数据 (Items)
        self._process_type_data(cursor, "items", "items.json", ItemTemplate, self._insert_item)

        # 2. 合并实体数据 (Entities)
        self._process_type_data(
            cursor,
            "entities",
            "entities.json",
            EntityTemplate,
            self._insert_entity,
        )

    def _process_type_data(
        self,
        cursor: sqlite3.Cursor,
        data_type: str,
        filename: str,
        model_cls: type[BaseModel],
        insert_func: Callable[[sqlite3.Cursor, Any], None],
    ) -> None:
        """
        功能：通用数据合并处理器。
        入参：cursor；data_type；filename；model_cls；insert_func。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        # A. 加载官方本体基准
        base_path = os.path.join(DATA_DIR, filename)
        master_data = {} # key: id, value: dict

        if os.path.exists(base_path):
            with open(base_path, encoding="utf-8") as f:
                raw = json.load(f)
                id_key = "item_id" if data_type == "items" else "entity_id"
                master_data = {item[id_key]: item for item in raw}

        # B. 按优先级迭代 MOD 进行合并
        for mod in self.mod_registry:
            mod_data_path = os.path.join(MODS_DIR, mod['mod_id'], "data", filename)
            if not os.path.exists(mod_data_path):
                continue

            logger.info(f"正在合并 MOD [{mod['mod_id']}] 的 {data_type} 数据...")
            with open(mod_data_path, encoding="utf-8") as f:
                mod_raw = json.load(f)

            strategy = mod.get("conflict_strategy", "smart_merge")
            allowed = mod.get("allowed_fields", [])
            id_key = "item_id" if data_type == "items" else "entity_id"

            for mod_item in mod_raw:
                target_id = mod_item.get(id_key)
                if not target_id:
                    continue

                if target_id not in master_data:
                    # 如果是新 ID，直接加入 (但需符合 Pydantic 校验)
                    master_data[target_id] = mod_item
                else:
                    # 如果是已有 ID，执行冲突策略
                    if strategy == "strict_override":
                        master_data[target_id] = mod_item
                    else:
                        master_data[target_id] = deep_merge(
                            master_data[target_id],
                            mod_item,
                            allowed,
                        )

        # C. 终极验证与持久化
        success_count = 0
        for target_id, final_dict in master_data.items():
            try:
                # Pydantic 契约校验
                validated_obj = model_cls.model_validate(final_dict)
                # 写入数据库
                insert_func(cursor, validated_obj)
                success_count += 1
            except Exception as e:
                logger.error(f"数据 [{target_id}] 合并后违反契约，已被拒绝加载: {e}")

        logger.info(f"成功导入 {success_count} 个 {data_type} 实体。")

    def _insert_item(self, cursor: sqlite3.Cursor, item: ItemTemplate) -> None:
        """
        功能：执行 `_insert_item` 相关业务逻辑。
        入参：cursor；item。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        cursor.execute("""
            INSERT OR REPLACE INTO items
            (item_id, name, description, item_type, min_strength, min_agility, min_intelligence,
             effects_json, hooks_json, weight, rarity, usage_limit, is_stackable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.item_id, item.name, item.description, item.item_type,
            item.requirements.min_strength,
            item.requirements.min_agility,
            item.requirements.min_intelligence,
            json.dumps([e.model_dump() for e in item.effects], ensure_ascii=False),
            json.dumps(item.hooks, ensure_ascii=False),
            item.weight, item.rarity, item.usage_limit, item.is_stackable
        ))

    def _insert_entity(self, cursor: sqlite3.Cursor, ent: EntityTemplate) -> None:
        """
        功能：执行 `_insert_entity` 相关业务逻辑。
        入参：cursor；ent。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        cursor.execute("""
            INSERT OR REPLACE INTO entities_active
            (
                entity_id,
                name,
                entity_type,
                description,
                strength,
                agility,
                intelligence,
                constitution,
                hp,
                max_hp,
                mp,
                max_mp,
                traits_json,
                social_relations_json,
                current_location_id,
                behavior_pattern,
                state_flags_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ent.entity_id, ent.name, ent.entity_type, ent.description,
            ent.base_stats.strength,
            ent.base_stats.agility,
            ent.base_stats.intelligence,
            ent.base_stats.constitution,
            ent.resources.hp, ent.resources.max_hp, ent.resources.mp, ent.resources.max_mp,
            json.dumps(ent.traits, ensure_ascii=False),
            json.dumps(ent.social_relations, ensure_ascii=False),
            ent.current_location_id, ent.behavior_pattern,
            json.dumps(ent.state_flags, ensure_ascii=False)
        ))
        cursor.execute("DELETE FROM inventory_active WHERE owner_id = ?", (ent.entity_id,))
        for item_id in ent.default_inventory:
            cursor.execute(
                """
                INSERT INTO inventory_active (owner_id, item_id, quantity)
                VALUES (?, ?, 1)
                ON CONFLICT(owner_id, item_id)
                DO UPDATE SET quantity = quantity + 1
                """,
                (ent.entity_id, item_id),
            )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initializer = DBInitializer()
    initializer.initialize_db()
