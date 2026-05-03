from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from typing import Any

import pytest
from flask import Flask

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.blueprints.memory import memory_blueprint
from web_api.blueprints.runtime import runtime_blueprint
from web_api.blueprints.sessions import sessions_blueprint
from web_api.service import (
    ApiRuntimeContext,
    TurnExecutionError,
    _character_exists,
    _enrich_character_inventory,
    _ensure_runtime_schema_ready,
    _ensure_vector_index_ready,
    _load_item_catalog,
    build_initial_turn_payload,
    ensure_character_available,
    get_play_state,
    get_runtime_context,
    log_post_body,
    now_iso,
    run_turn,
    validate_character_id,
    validate_request_id,
    validate_session_id,
)
from web_api.session_store import WebSessionStore


class _BlueprintRuntimeContext(ApiRuntimeContext):
    """
    功能：为 sessions/memory/runtime 蓝图测试提供最小运行时上下文，隔离主循环依赖。
    入参：db_path（str）：测试 SQLite 路径。
    出参：_BlueprintRuntimeContext。
    异常：数据库初始化失败时向上抛出。
    """

    def __init__(self, db_path: str) -> None:
        """
        功能：初始化测试会话存储与会话锁容器。
        入参：db_path（str）：SQLite 文件路径。
        出参：None。
        异常：底层 SQLite 初始化异常向上抛出。
        """
        super().__init__()
        self.main_loop = object()
        self.session_store = WebSessionStore(db_path)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：提供会话级锁，保持与生产路径一致的串行语义。
        入参：session_id（str）：会话 ID。
        出参：threading.Lock。
        异常：无显式异常。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _init_runtime_db(db_path: str) -> None:
    """
    功能：初始化运行态表结构，支持会话、回合、幂等测试。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：SQL 异常向上抛出。
    """
    import sqlite3

    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


@pytest.fixture
def api_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Generator[Any]:
    """
    功能：构建 sessions/memory/runtime 联合测试客户端，并注入可控的运行时依赖。
    入参：tmp_path；monkeypatch。
    出参：Flask test client。
    异常：运行态表初始化失败时向上抛出。
    """
    db_path = str(tmp_path / "runtime_blueprints.db")
    _init_runtime_db(db_path)
    context = _BlueprintRuntimeContext(db_path)
    context.session_store.create_session(
        session_id="sess_a1scope01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso=now_iso(),
        memory_policy={"mode": "auto", "max_turns": 20},
    )

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(sessions_blueprint)
    app.register_blueprint(memory_blueprint)
    app.register_blueprint(runtime_blueprint)

    monkeypatch.setattr("web_api.blueprints.sessions.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr(
        "web_api.blueprints.sessions.build_initial_turn_payload",
        lambda _cid, _sandbox_mode: {
            "active_character": {"id": "player_01", "inventory": []},
            "scene_snapshot": {"schema_version": "scene_snapshot.v2", "affordances": []},
            "final_response": "开场叙事",
            "quick_actions": ["观察周围"],
            "affordances": [],
            "failure_reason": "",
            "suggested_next_step": "观察周围",
            "outcome": "initial_scene",
        },
    )
    monkeypatch.setattr(
        "web_api.blueprints.sessions.get_play_state",
        lambda _cid, _sandbox_mode, recent_memory="": {
            "active_character": {"id": "player_01", "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "recent_memory": recent_memory,
            },
        },
    )
    yield app.test_client()


def test_sessions_create_and_get_detail(api_client) -> None:
    """
    功能：验证创建会话与查询详情路径可用，并覆盖 create/get 的核心字段组装逻辑。
    入参：api_client（fixture）：测试客户端。
    出参：None。
    异常：断言失败表示 sessions 蓝图主路径回归。
    """
    create_resp = api_client.post(
        "/api/sessions",
        json={"request_id": "req_a1sess01", "character_id": "player_01", "sandbox_mode": False},
    )
    create_body = create_resp.get_json()
    assert create_resp.status_code == 201
    assert create_body["session_id"].startswith("sess_")
    assert create_body["outcome"] == "initial_scene"

    detail_resp = api_client.get(f"/api/sessions/{create_body['session_id']}")
    detail_body = detail_resp.get_json()
    assert detail_resp.status_code == 200
    assert detail_body["session_id"] == create_body["session_id"]
    assert detail_body["active_character"]["id"] == "player_01"


def test_memory_routes_cover_summary_raw_and_refresh(api_client) -> None:
    """
    功能：验证 memory 查询 summary/raw 与 refresh 幂等主路径，覆盖摘要重建和策略更新逻辑。
    入参：api_client（fixture）：测试客户端。
    出参：None。
    异常：断言失败表示 memory 蓝图行为回归。
    """
    summary_resp = api_client.get("/api/sessions/sess_a1scope01/memory?format=summary")
    raw_resp = api_client.get("/api/sessions/sess_a1scope01/memory?format=raw")
    assert summary_resp.status_code == 200
    assert raw_resp.status_code == 200

    first_refresh = api_client.post(
        "/api/sessions/sess_a1scope01/memory/refresh",
        json={"request_id": "req_a1mem01", "max_turns": 20},
    )
    second_refresh = api_client.post(
        "/api/sessions/sess_a1scope01/memory/refresh",
        json={"request_id": "req_a1mem01", "max_turns": 20},
    )
    first_body = first_refresh.get_json()
    second_body = second_refresh.get_json()
    assert first_refresh.status_code == 200
    assert second_refresh.status_code == 200
    assert first_body["session_id"] == "sess_a1scope01"
    assert first_body["summary"] == second_body["summary"]


def test_runtime_reset_session_idempotent(api_client) -> None:
    """
    功能：验证 reset 路由执行成功后可被同 request_id 幂等复用，避免重复重置。
    入参：api_client（fixture）：测试客户端。
    出参：None。
    异常：断言失败表示 runtime.reset 幂等契约回归。
    """
    first = api_client.post(
        "/api/sessions/sess_a1scope01/reset",
        json={"request_id": "req_a1reset01", "keep_character": True},
    )
    second = api_client.post(
        "/api/sessions/sess_a1scope01/reset",
        json={"request_id": "req_a1reset01", "keep_character": True},
    )
    first_body = first.get_json()
    second_body = second.get_json()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_body["reset"] is True
    assert first_body["current_session_turn_id"] == 0
    assert second_body["current_session_turn_id"] == 0


def test_sessions_memory_runtime_idempotent_have_log_evidence(
    api_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 sessions/memory/runtime 的幂等命中分支会输出日志，作为回归证据链的一部分。
    入参：api_client（fixture）；caplog（日志捕获器）。
    出参：None。
    异常：断言失败表示业务幂等命中缺少可审计日志。
    """
    caplog.set_level(logging.INFO, logger="WebAPI.Runtime")

    api_client.post(
        "/api/sessions",
        json={"request_id": "req_a1idem_log01", "character_id": "player_01"},
    )
    api_client.post(
        "/api/sessions",
        json={"request_id": "req_a1idem_log01", "character_id": "player_01"},
    )
    api_client.post(
        "/api/sessions/sess_a1scope01/memory/refresh",
        json={"request_id": "req_a1idem_log02", "max_turns": 20},
    )
    api_client.post(
        "/api/sessions/sess_a1scope01/memory/refresh",
        json={"request_id": "req_a1idem_log02", "max_turns": 20},
    )
    api_client.post(
        "/api/sessions/sess_a1scope01/reset",
        json={"request_id": "req_a1idem_log03", "keep_character": True},
    )
    api_client.post(
        "/api/sessions/sess_a1scope01/reset",
        json={"request_id": "req_a1idem_log03", "keep_character": True},
    )

    assert "create_session 幂等命中" in caplog.text
    assert "refresh_memory 幂等命中" in caplog.text
    assert "reset_session 幂等命中" in caplog.text


def test_log_post_body_fallback_has_log_evidence(caplog: pytest.LogCaptureFixture) -> None:
    """
    功能：验证 POST 入参日志在 JSON 序列化失败时走 repr 降级，并留下日志证据。
    入参：caplog（pytest.LogCaptureFixture）：日志捕获器。
    出参：None。
    异常：断言失败表示日志降级路径不可观测。
    """
    caplog.set_level(logging.INFO, logger="WebAPI.Runtime")
    log_post_body("turns.create", {"request_id": "req_a1log01", "bad": {1, 2}})
    assert "POST 请求体: route=turns.create" in caplog.text
    assert "bad" in caplog.text


def test_ensure_character_available_self_heal_has_warning_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证默认角色缺失时会记录自愈告警日志，并触发一次种子初始化。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示角色自愈日志证据缺失。
    """
    state = {"checks": 0}

    def fake_character_exists(character_id: str) -> bool:
        state["checks"] += 1
        if character_id != "player_01":
            return False
        return state["checks"] >= 2

    class _FakeInitializer:
        def initialize_db(self) -> None:
            return None

    monkeypatch.setattr("web_api.service._character_exists", fake_character_exists)
    monkeypatch.setattr("web_api.service.DBInitializer", _FakeInitializer)
    caplog.set_level(logging.WARNING, logger="WebAPI.Runtime")

    assert ensure_character_available("player_01") is True
    assert "默认角色 player_01 缺失，尝试重新导入种子数据自愈" in caplog.text


def test_load_item_catalog_failure_has_exception_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证物品目录加载失败时会记录异常日志并降级为空目录。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示目录降级路径缺失日志证据。
    """
    monkeypatch.setattr("web_api.service.ITEMS_DATA_PATH", "D:/not-exists/items.json")
    caplog.set_level(logging.ERROR, logger="WebAPI.Runtime")
    catalog = _load_item_catalog()
    assert catalog == {}
    assert "物品目录读取失败，背包展示降级为物品 ID" in caplog.text


def test_run_turn_timeout_has_error_log_evidence(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 run_turn 超时分支会抛出 TURN_TIMEOUT，并记录可检索的错误日志。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示超时链路缺失日志证据或错误码漂移。
    """

    class _FakeMainLoop:
        async def run(self, **kwargs: Any) -> dict[str, Any]:
            return {"final_response": "unused"}

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = _FakeMainLoop()

    async def _raise_timeout(awaitable: Any, timeout: float) -> Any:
        # 降级路径：主动关闭协程，避免测试桩导致“协程未等待”告警污染验收日志。
        awaitable.close()
        raise TimeoutError("timeout")

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())
    monkeypatch.setattr("web_api.service.asyncio.wait_for", _raise_timeout)
    caplog.set_level(logging.ERROR, logger="WebAPI.Runtime")

    with pytest.raises(TurnExecutionError) as exc_info:
        run_turn(
            session={"session_id": "sess_a1scope01", "memory_summary": ""},
            user_input="观察周围",
            character_id="player_01",
            sandbox_mode=False,
            trace_id="trc_timeout_001",
            request_id="req_timeout01",
        )
    assert exc_info.value.error_code == "TURN_TIMEOUT"
    assert exc_info.value.status_code == 504
    assert "TurnTrace[trc_timeout_001] run_turn_timeout" in caplog.text


def test_run_turn_unexpected_error_has_log_evidence(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 run_turn 非超时异常会转换为 INTERNAL_ERROR，并记录失败日志证据。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示异常降级链路不可观测。
    """

    class _FakeMainLoop:
        async def run(self, **kwargs: Any) -> dict[str, Any]:
            return {"final_response": "unused"}

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = _FakeMainLoop()

    async def _raise_runtime_error(awaitable: Any, timeout: float) -> Any:
        # 降级路径：主动关闭协程，避免测试桩导致“协程未等待”告警污染验收日志。
        awaitable.close()
        raise RuntimeError("boom")

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())
    monkeypatch.setattr("web_api.service.asyncio.wait_for", _raise_runtime_error)
    caplog.set_level(logging.ERROR, logger="WebAPI.Runtime")

    with pytest.raises(TurnExecutionError) as exc_info:
        run_turn(
            session={"session_id": "sess_a1scope01", "memory_summary": ""},
            user_input="观察周围",
            character_id="player_01",
            sandbox_mode=False,
            trace_id="trc_failed_001",
            request_id="req_failed01",
        )
    assert exc_info.value.error_code == "INTERNAL_ERROR"
    assert exc_info.value.status_code == 500
    assert "TurnTrace[trc_failed_001] run_turn_failed" in caplog.text


def test_get_play_state_and_build_initial_turn_payload_cover_main_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 get_play_state 与 build_initial_turn_payload 的主路径组装逻辑，覆盖场景与首回合负载。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 service 首屏状态构建逻辑回归。
    """

    class _FakeGMAgent:
        def render(self, state: dict[str, Any]) -> str:
            return f"开场:{state['action_intent']['type']}"

        def suggest_quick_actions(self, state: dict[str, Any], final_response: str) -> list[str]:
            return ["观察周围", final_response]

    class _FakeMainLoop:
        def __init__(self) -> None:
            self.gm_agent = _FakeGMAgent()

        def _build_character_state(
            self,
            character_id: str,
            use_shadow: bool = False,
        ) -> dict[str, Any]:
            return {"id": character_id, "inventory": ["health_potion_01"]}

        def _build_scene_snapshot(
            self,
            active_character: Any,
            recent_memory: str = "",
            use_shadow: bool = False,
        ) -> dict[str, Any]:
            return {
                "schema_version": "scene_snapshot.v2",
                "recent_memory": recent_memory,
                "affordances": [{"enabled": True, "user_input": "观察周围"}],
            }

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = _FakeMainLoop()

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())
    monkeypatch.setattr(
        "web_api.service._load_item_catalog",
        lambda: {
            "health_potion_01": {
                "name": "治疗药水",
                "description": "恢复生命",
                "item_type": "consumable",
            }
        },
    )

    play_state = get_play_state("player_01", sandbox_mode=False, recent_memory="上回合摘要")
    assert play_state["active_character"]["inventory_items"][0]["name"] == "治疗药水"
    assert play_state["scene_snapshot"]["schema_version"] == "scene_snapshot.v2"

    initial_payload = build_initial_turn_payload(
        "player_01",
        sandbox_mode=False,
        recent_memory="上回合摘要",
    )
    assert initial_payload["final_response"].startswith("开场:")
    assert initial_payload["quick_actions"][0] == "观察周围"
    assert initial_payload["outcome"] == "initial_scene"


def test_get_play_state_raises_when_main_loop_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证主循环未初始化时 get_play_state 会抛出 RuntimeError，避免返回脏状态。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示运行时就绪检查失效。
    """

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = None

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())
    with pytest.raises(RuntimeError, match="主循环未初始化"):
        get_play_state("player_01", sandbox_mode=False)


def test_get_runtime_context_raises_when_extension_missing() -> None:
    """
    功能：验证 Flask 未挂载 API 运行时上下文时会抛出明确 RuntimeError。
    入参：无，使用临时 Flask app 上下文。
    出参：None。
    异常：断言失败表示运行时上下文缺失校验回归。
    """
    app = Flask(__name__)

    with app.app_context(), pytest.raises(RuntimeError, match="API 运行时上下文未初始化"):
        get_runtime_context()


def test_get_play_state_raises_when_character_state_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证角色状态缺失时 get_play_state 会抛出带 character_id 的异常，避免静默返回空角色。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示角色状态缺失链路被错误降级。
    """

    class _FakeMainLoop:
        def _build_character_state(self, character_id: str, use_shadow: bool = False) -> None:
            return None

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = _FakeMainLoop()

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())

    with pytest.raises(RuntimeError, match="角色状态不存在: character_id=missing_player"):
        get_play_state("missing_player", sandbox_mode=True)


def test_build_initial_turn_payload_raises_when_main_loop_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证首回合负载构建在主循环缺失时快速失败，避免 sessions 蓝图返回半初始化数据。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示首回合运行时就绪检查回归。
    """

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = None

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())

    with pytest.raises(RuntimeError, match="主循环未初始化"):
        build_initial_turn_payload("player_01", sandbox_mode=False)


def test_character_exists_queries_active_table(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 `_character_exists` 能正确读取 Active 表中的角色存在性。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示角色存在性检查回归。
    """
    import sqlite3

    db_path = tmp_path / "chars.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE entities_active (entity_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO entities_active(entity_id) VALUES (?)", ("player_01",))
        connection.commit()
    monkeypatch.setattr("web_api.service.DB_PATH", str(db_path))
    assert _character_exists("player_01") is True
    assert _character_exists("missing") is False


def test_enrich_character_inventory_fallback_unknown_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 `_enrich_character_inventory` 在未知物品时会降级为 ID 展示，避免前端字段缺失。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示背包降级路径回归。
    """
    monkeypatch.setattr("web_api.service._load_item_catalog", lambda: {})
    enriched = _enrich_character_inventory({"id": "player_01", "inventory": ["unknown_item"]})
    assert enriched["inventory_items"][0]["name"] == "unknown_item"
    assert enriched["inventory_items"][0]["item_type"] == "unknown"


def test_item_catalog_and_inventory_schema_fallbacks(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证物品目录非列表/坏项会降级，且 inventory 非列表时输出空展示列表。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示 inventory/物品目录 schema 降级回归。
    """
    catalog_path = tmp_path / "items.json"
    catalog_path.write_text(
        '[{"item_id":"potion","name":"药水"}, "bad", {"name":"missing_id"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr("web_api.service.ITEMS_DATA_PATH", str(catalog_path))

    catalog = _load_item_catalog()
    enriched = _enrich_character_inventory({"id": "player_01", "inventory": "bad-type"})

    assert list(catalog) == ["potion"]
    assert enriched["inventory_items"] == []


def test_validate_id_helpers_cover_valid_and_invalid_inputs() -> None:
    """
    功能：验证 request/session/character 三类 ID 校验函数在合法与非法输入下行为稳定。
    入参：无。
    出参：None。
    异常：断言失败表示 API 参数校验契约回归。
    """
    assert validate_request_id({"request_id": "req_valid_01"}) == "req_valid_01"
    assert validate_request_id({"request_id": "bad space"}) is None
    assert validate_request_id({"request_id": 123}) is None
    assert validate_session_id("sess_valid_01") is True
    assert validate_session_id("bad space") is False
    assert validate_character_id("player_01") is True
    assert validate_character_id("x") is False


def test_api_runtime_context_reuses_session_lock() -> None:
    """
    功能：验证 ApiRuntimeContext 会复用同一 session_id 的锁对象，确保会话内串行语义不漂移。
    入参：无。
    出参：None。
    异常：断言失败表示运行时并发保护回归。
    """
    context = ApiRuntimeContext()
    first = context.get_session_lock("sess_a1scope01")
    second = context.get_session_lock("sess_a1scope01")
    third = context.get_session_lock("sess_a1scope02")
    assert first is second
    assert first is not third


def test_ensure_runtime_schema_ready_executes_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证运行时 schema 补齐函数会调用迁移入口并提交事务。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示运行时表结构补齐流程回归。
    """
    calls = {"committed": False, "closed": False, "cursor_obj": object()}

    class _FakeConnection:
        def cursor(self) -> object:
            return calls["cursor_obj"]

        def commit(self) -> None:
            calls["committed"] = True

        def close(self) -> None:
            calls["closed"] = True

    seen = {"cursor": None}

    def _fake_ensure_runtime_tables(cursor: object) -> None:
        seen["cursor"] = cursor

    monkeypatch.setattr("web_api.service.sqlite3.connect", lambda _path: _FakeConnection())
    monkeypatch.setattr("web_api.service.ensure_runtime_tables", _fake_ensure_runtime_tables)
    _ensure_runtime_schema_ready("D:/fake/runtime.db")
    assert seen["cursor"] is calls["cursor_obj"]
    assert calls["committed"] is True
    assert calls["closed"] is True


def test_ensure_vector_index_ready_has_log_evidence_for_init_flow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证向量索引缺失时会触发初始化流程，并记录 warning/info 日志证据。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 RAG 初始化可观测性回归。
    """
    state = {"exists_calls": 0, "updated": False}

    def _fake_exists(_path: str) -> bool:
        state["exists_calls"] += 1
        return state["exists_calls"] >= 2

    class _FakeRAGManager:
        def update_index(self) -> None:
            state["updated"] = True

    monkeypatch.setattr("web_api.service.os.path.exists", _fake_exists)
    monkeypatch.setattr("tools.rag.RAGManager", _FakeRAGManager)
    caplog.set_level(logging.INFO, logger="WebAPI.Runtime")

    _ensure_vector_index_ready()
    assert state["updated"] is True
    assert "检测到向量库缺失，开始自动初始化 RAG 索引" in caplog.text
    assert "向量库初始化完成" in caplog.text


def test_ensure_vector_index_ready_raises_when_docstore_missing_after_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证索引初始化后仍缺少 docstore 时抛 RuntimeError，阻止服务在不一致状态下启动。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示索引完整性校验失效。
    """

    class _FakeRAGManager:
        def update_index(self) -> None:
            return None

    monkeypatch.setattr("web_api.service.os.path.exists", lambda _path: False)
    monkeypatch.setattr("tools.rag.RAGManager", _FakeRAGManager)
    with pytest.raises(RuntimeError, match="向量库初始化后仍未生成 docstore.json"):
        _ensure_vector_index_ready()


def test_run_turn_postprocess_malformed_result_records_failed_trace_stages(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 run_turn 后处理遇到空角色/空场景/缺 action 时仍返回稳定负载并记录 failed trace 阶段。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 run_turn 后处理降级、trace 阶段或日志证据回归。
    """

    class _FakeOuterBridge:
        pass

    class _FakeMainLoop:
        def __init__(self) -> None:
            self.outer_bridge = _FakeOuterBridge()

        async def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
            return {
                "active_character": "bad-type",
                "scene_snapshot": "bad-type",
                "action_intent": None,
                "physics_diff": None,
                "is_valid": False,
                "final_response": "",
                "quick_actions": ["观察周围"],
                "turn_outcome": "clarification",
                "clarification_question": "你想做什么？",
                "failure_reason": "缺少动作",
                "suggested_next_step": "补充动作",
                "validation_errors": ["缺少动作"],
                "runtime_turn_id": 3,
                "outer_emit_result": {"status": "weird", "detail": "bad"},
            }

    class _FakeContext:
        def __init__(self) -> None:
            self.main_loop = _FakeMainLoop()

    monkeypatch.setattr("web_api.service.get_runtime_context", lambda: _FakeContext())
    monkeypatch.setattr("web_api.service._load_item_catalog", lambda: {})
    caplog.set_level(logging.INFO, logger="WebAPI.Runtime")

    payload = run_turn(
        session={"session_id": "sess_a1scope01", "memory_summary": "上一回合摘要"},
        user_input="",
        character_id="player_01",
        sandbox_mode=False,
        trace_id="trc_post_001",
        request_id="req_post01",
    )

    stages = {stage["stage"]: stage for stage in payload["trace"]["stages"]}
    assert payload["active_character"]["inventory_items"] == []
    assert isinstance(payload["scene_snapshot"]["affordances"], list)
    assert payload["scene_snapshot"]["affordances"]
    assert payload["outcome"] == "clarification"
    assert stages["scene.loaded"]["status"] == "failed"
    assert stages["nlu.parsed"]["status"] == "failed"
    assert stages["action.resolved"]["status"] == "skipped"
    assert stages["state.updated"]["status"] == "skipped"
    assert stages["gm.rendered"]["status"] == "failed"
    assert stages["outer.emitted"]["status"] == "skipped"
    assert "TurnTrace[trc_post_001] stages=" in caplog.text
