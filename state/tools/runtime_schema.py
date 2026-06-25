"""
功能：导出运行时契约模型的 JSON Schema 生成辅助逻辑。
"""

import sqlite3


def _table_has_column(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """
    功能：判断目标表是否包含指定列。
    入参：cursor；table；column。
    出参：bool。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _add_missing_columns(
    cursor: sqlite3.Cursor,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    """
    功能：为旧数据库补齐指定表缺失的列。
    入参：cursor（sqlite3.Cursor）：迁移游标；table（str）：目标表；
        columns（tuple[tuple[str, str], ...]）：列名与 ALTER TABLE 片段。
    出参：None。
    异常：PRAGMA 或 ALTER TABLE 失败时向上抛出，避免半迁移数据库被误判可用。
    """
    for column_name, column_sql in columns:
        if not _table_has_column(cursor, table, column_name):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")


def _ensure_outer_event_tables(cursor: sqlite3.Cursor) -> None:
    """
    功能：创建并迁移外环事件 outbox 表及其调度索引。
    入参：cursor（sqlite3.Cursor）：运行时数据库迁移游标。
    出参：None。
    异常：SQL 执行失败时向上抛出，由数据库初始化流程中止启动。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS outer_event_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            dead_lettered_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    _add_missing_columns(
        cursor,
        "outer_event_outbox",
        (
            ("next_retry_at", "next_retry_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
            ("dead_lettered_at", "dead_lettered_at DATETIME"),
        ),
    )
    # 外环补偿队列是回合响应后的高频后台路径；索引覆盖 pending/retrying 取数与卡住任务回收。
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_outer_outbox_status_retry_id
        ON outer_event_outbox(status, next_retry_at, id)
        """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_outer_outbox_processing_updated
        ON outer_event_outbox(status, updated_at)
        """)


def _ensure_web_sessions_table(cursor: sqlite3.Cursor) -> None:
    """
    功能：创建并迁移 Web 会话主表。
    入参：cursor（sqlite3.Cursor）：运行时数据库迁移游标。
    出参：None。
    异常：SQL 执行失败时向上抛出；调用方不得继续使用未完成迁移的表。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_sessions (
            session_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            sandbox_mode INTEGER NOT NULL DEFAULT 0,
            current_turn_id INTEGER NOT NULL DEFAULT 0,
            memory_summary TEXT NOT NULL DEFAULT '',
            memory_policy_json TEXT NOT NULL DEFAULT '{"mode":"auto","max_turns":20}',
            pack_id TEXT,
            scenario_id TEXT,
            pack_version TEXT,
            compiled_artifact_hash TEXT,
            persona_profile_json TEXT NOT NULL DEFAULT '{}',
            session_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    # A2-Core 迁移边界：旧会话允许 pack 字段为空，表示继续使用 engine default 内容层。
    _add_missing_columns(
        cursor,
        "web_sessions",
        (
            ("pack_id", "pack_id TEXT"),
            ("scenario_id", "scenario_id TEXT"),
            ("pack_version", "pack_version TEXT"),
            ("compiled_artifact_hash", "compiled_artifact_hash TEXT"),
            ("persona_profile_json", "persona_profile_json TEXT NOT NULL DEFAULT '{}'"),
            ("session_metadata_json", "session_metadata_json TEXT NOT NULL DEFAULT '{}'"),
            ("base_character_id", "base_character_id TEXT"),
            ("runtime_character_id", "runtime_character_id TEXT"),
        ),
    )
    cursor.execute("""
        UPDATE web_sessions
        SET base_character_id = character_id
        WHERE base_character_id IS NULL OR base_character_id = ''
        """)
    cursor.execute("""
        UPDATE web_sessions
        SET runtime_character_id = character_id
        WHERE runtime_character_id IS NULL OR runtime_character_id = ''
        """)
    # 会话选择器按最近活动时间倒序取少量记录；缺少该索引时保存进度增多会退化为全表扫描排序。
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_sessions_last_active_created
        ON web_sessions(last_active_at DESC, created_at DESC)
        """)


def _ensure_web_session_turns_table(cursor: sqlite3.Cursor) -> None:
    """
    功能：创建并迁移 Web 回合历史表及其幂等索引。
    入参：cursor（sqlite3.Cursor）：运行时数据库迁移游标。
    出参：None。
    异常：SQL 执行失败时向上抛出；调用方不得继续使用未完成迁移的表。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_session_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            user_input TEXT NOT NULL,
            is_valid INTEGER NOT NULL DEFAULT 0,
            action_intent_json TEXT,
            physics_diff_json TEXT,
            trigger_events_json TEXT,
            quest_updates_json TEXT,
            quest_states_json TEXT,
            branch_consequences_json TEXT,
            final_response TEXT NOT NULL,
            memory_summary TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, turn_id)
        )
        """)
    _add_missing_columns(
        cursor,
        "web_session_turns",
        (
            ("trigger_events_json", "trigger_events_json TEXT"),
            ("quest_updates_json", "quest_updates_json TEXT"),
            ("quest_states_json", "quest_states_json TEXT"),
            ("branch_consequences_json", "branch_consequences_json TEXT"),
        ),
    )
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_turns_session_id
        ON web_session_turns(session_id)
        """)
    # 迁移边界：历史版本未对 (session_id, request_id) 加唯一约束。
    # 这里先去重保留最早写入的一条，再创建唯一索引兜底幂等语义。
    cursor.execute("""
        DELETE FROM web_session_turns
        WHERE id IN (
            SELECT newer.id
            FROM web_session_turns AS newer
            JOIN web_session_turns AS older
              ON newer.session_id = older.session_id
             AND newer.request_id = older.request_id
             AND newer.id > older.id
        )
        """)
    cursor.execute("DROP INDEX IF EXISTS idx_web_turns_session_req")
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_web_turns_session_req_unique
        ON web_session_turns(session_id, request_id)
        """)


def _ensure_web_session_tables(cursor: sqlite3.Cursor) -> None:
    """
    功能：按依赖顺序创建并迁移 Web 会话与回合历史表。
    入参：cursor（sqlite3.Cursor）：运行时数据库迁移游标。
    出参：None。
    异常：SQL 执行失败时向上抛出，避免部分迁移的 Web 表继续被使用。
    """
    _ensure_web_sessions_table(cursor)
    _ensure_web_session_turns_table(cursor)


def _ensure_memory_and_control_tables(cursor: sqlite3.Cursor) -> None:
    """
    功能：创建长期叙事记忆、幂等缓存和沙盒锁相关表。
    入参：cursor（sqlite3.Cursor）：运行时数据库迁移游标。
    出参：None。
    异常：SQL 执行失败时向上抛出，防止控制表缺失时继续服务请求。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_session_memory_items (
            memory_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'session',
            kind TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            text TEXT NOT NULL,
            evidence_turn_id INTEGER NOT NULL,
            importance INTEGER NOT NULL DEFAULT 3,
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_turn_id INTEGER NOT NULL,
            last_seen_turn_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, kind, subject_type, subject_id, text)
        )
        """)
    # 长期记忆检索路径：按会话读取 active 条目，再按对象/类型裁剪给 GM。
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_memory_session_status
        ON web_session_memory_items(session_id, status, importance DESC, last_seen_turn_id DESC)
        """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_memory_subject
        ON web_session_memory_items(session_id, subject_type, subject_id, status)
        """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, session_id, request_id)
        )
        """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_sandbox_lock (
            lock_id INTEGER PRIMARY KEY CHECK (lock_id = 1),
            owner_session_id TEXT,
            acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    cursor.execute("""
        INSERT OR IGNORE INTO web_sandbox_lock(lock_id, owner_session_id)
        VALUES (1, NULL)
        """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_web_idempotency_scope_session
        ON web_idempotency_keys(scope, session_id)
        """)


def ensure_runtime_tables(cursor: sqlite3.Cursor) -> None:
    """
    功能：统一创建并迁移运行期依赖的最小表结构。
    入参：cursor。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS world_state_active (
            key TEXT PRIMARY KEY,
            value_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS world_state_shadow (
            key TEXT PRIMARY KEY,
            value_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS timeline (
            id INTEGER PRIMARY KEY CHECK (id = 0),
            current_time_minutes INTEGER DEFAULT 0,
            total_turns INTEGER DEFAULT 0
        )
        """)
    cursor.execute(
        "INSERT OR IGNORE INTO timeline (id, current_time_minutes, total_turns) VALUES (0, 0, 0)"
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS achievement_unlocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            achievement_id TEXT NOT NULL,
            description TEXT,
            reward_json TEXT,
            unlocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_id, achievement_id)
        )
        """)
    _ensure_outer_event_tables(cursor)
    _ensure_web_session_tables(cursor)
    _ensure_memory_and_control_tables(cursor)
