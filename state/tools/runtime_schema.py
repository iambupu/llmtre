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


def ensure_runtime_tables(cursor: sqlite3.Cursor) -> None:
    """
    功能：统一创建并迁移运行期依赖的最小表结构。
    入参：cursor。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS world_state_active (
            key TEXT PRIMARY KEY,
            value_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS world_state_shadow (
            key TEXT PRIMARY KEY,
            value_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline (
            id INTEGER PRIMARY KEY CHECK (id = 0),
            current_time_minutes INTEGER DEFAULT 0,
            total_turns INTEGER DEFAULT 0
        )
        """
    )
    cursor.execute(
        "INSERT OR IGNORE INTO timeline (id, current_time_minutes, total_turns) VALUES (0, 0, 0)"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS achievement_unlocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            achievement_id TEXT NOT NULL,
            description TEXT,
            reward_json TEXT,
            unlocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_id, achievement_id)
        )
        """
    )
    cursor.execute(
        """
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
        """
    )
    if not _table_has_column(cursor, "outer_event_outbox", "next_retry_at"):
        cursor.execute(
            """
            ALTER TABLE outer_event_outbox
            ADD COLUMN next_retry_at DATETIME DEFAULT CURRENT_TIMESTAMP
            """
        )
    if not _table_has_column(cursor, "outer_event_outbox", "dead_lettered_at"):
        cursor.execute(
            """
            ALTER TABLE outer_event_outbox
            ADD COLUMN dead_lettered_at DATETIME
            """
        )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_sessions (
            session_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            sandbox_mode INTEGER NOT NULL DEFAULT 0,
            current_turn_id INTEGER NOT NULL DEFAULT 0,
            memory_summary TEXT NOT NULL DEFAULT '',
            memory_policy_json TEXT NOT NULL DEFAULT '{"mode":"auto","max_turns":20}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_session_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            user_input TEXT NOT NULL,
            is_valid INTEGER NOT NULL DEFAULT 0,
            action_intent_json TEXT,
            physics_diff_json TEXT,
            final_response TEXT NOT NULL,
            memory_summary TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, turn_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_turns_session_id
        ON web_session_turns(session_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_turns_session_req
        ON web_session_turns(session_id, request_id)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, session_id, request_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_idempotency_scope_session
        ON web_idempotency_keys(scope, session_id)
        """
    )
