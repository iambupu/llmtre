from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask

from state.tools.runtime_schema import ensure_runtime_tables
from tools.packs.registry import StoryPackRegistry
from web_api.blueprints.sessions import sessions_blueprint
from web_api.service import ApiRuntimeContext
from web_api.session_store import WebSessionStore


class _A2RuntimeContext(ApiRuntimeContext):
    """
    功能：为 A2 sessions 测试提供真实 session_store 与可控 Story Pack registry。
    入参：db_path（str）：SQLite 路径；registry（StoryPackRegistry）：测试 registry。
    出参：_A2RuntimeContext。
    异常：无额外异常。
    """

    def __init__(self, db_path: str, registry: StoryPackRegistry) -> None:
        """
        功能：初始化会话存储、registry 与会话锁。
        入参：db_path（str）：SQLite 文件；registry（StoryPackRegistry）：剧本包 registry。
        出参：None。
        异常：父类初始化或 sqlite 后续使用失败时向上抛出。
        """
        super().__init__()
        self.session_store = WebSessionStore(db_path)
        self.story_pack_registry = registry
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：返回会话级锁，保持与生产路径一致的串行语义。
        入参：session_id（str）：会话 ID。
        出参：threading.Lock。
        异常：无。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _make_case_root(name: str) -> Path:
    """
    功能：创建 A2 session 测试自管目录，避开 Windows tmp_path 权限噪声。
    入参：name（str）：用例名前缀。
    出参：Path，已创建目录。
    异常：目录创建失败时向上抛出。
    """
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _init_runtime_db(db_path: Path) -> None:
    """
    功能：初始化 Web runtime SQLite schema。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def _client(case_root: Path, monkeypatch: Any) -> Any:
    """
    功能：构造注册 sessions 蓝图的 A2 测试客户端。
    入参：case_root（Path）：测试根目录；monkeypatch（Any）：pytest monkeypatch。
    出参：FlaskClient。
    异常：Flask 或 SQLite 初始化失败时向上抛出。
    """
    packs_root = case_root / "story_packs"
    shutil.copytree("story_packs/demo_a2_core", packs_root / "demo_a2_core")
    registry = StoryPackRegistry(packs_root)
    db_path = case_root / "runtime.db"
    _init_runtime_db(db_path)
    context = _A2RuntimeContext(str(db_path), registry)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(sessions_blueprint)

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
    return app.test_client()


def test_create_session_binds_story_pack_and_get_detail_returns_metadata(monkeypatch: Any) -> None:
    """
    功能：验证创建 session 时可绑定已校验 Story Pack，详情接口返回同一元数据。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 pack/session 绑定链路回归。
    """
    case_root = _make_case_root("session_pack_bind")
    try:
        client = _client(case_root, monkeypatch)
        create = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_pack_bind_01",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
                "scenario_id": "default",
                "persona_profile": {"name": "流浪药剂师"},
            },
        )
        created = create.get_json()
        detail = client.get(f"/api/sessions/{created['session_id']}")
        detailed = detail.get_json()

        assert create.status_code == 201
        assert created["pack_id"] == "demo_a2_core"
        assert created["scenario_id"] == "default"
        assert created["pack_version"] == "0.1.0"
        assert created["compiled_artifact_hash"]
        assert created["persona_profile"] == {"name": "流浪药剂师"}
        assert detail.status_code == 200
        assert detailed["pack_id"] == created["pack_id"]
        assert detailed["compiled_artifact_hash"] == created["compiled_artifact_hash"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_create_session_accepts_null_scenario_id_as_default(monkeypatch: Any) -> None:
    """
    功能：验证 OpenAPI 允许的 scenario_id=null 会按 default 入口绑定 pack。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 nullable API 契约与路由解析再次漂移。
    """
    case_root = _make_case_root("session_null_scenario")
    try:
        client = _client(case_root, monkeypatch)
        response = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_null_scenario_01",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
                "scenario_id": None,
            },
        )
        body = response.get_json()

        assert response.status_code == 201
        assert body["pack_id"] == "demo_a2_core"
        assert body["scenario_id"] == "default"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_create_session_pack_binding_is_idempotent(monkeypatch: Any) -> None:
    """
    功能：验证重复 request_id 命中 create_session 幂等缓存，不改变 pack 绑定结果。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 create_session 幂等或 pack 元数据缓存退化。
    """
    case_root = _make_case_root("session_pack_idem")
    try:
        client = _client(case_root, monkeypatch)
        first = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_pack_idem_01",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
            },
        )
        second = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_pack_idem_01",
                "character_id": "player_01",
                "pack_id": "missing_pack",
                "scenario_id": "missing",
                "persona_profile": "bad",
            },
        )
        first_body = first.get_json()
        second_body = second.get_json()

        assert first.status_code == 201
        assert second.status_code == 201
        first_body.pop("trace_id", None)
        second_body.pop("trace_id", None)
        assert second_body == first_body
        assert first_body["pack_id"] == "demo_a2_core"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_create_session_without_pack_keeps_a1_behavior(monkeypatch: Any) -> None:
    """
    功能：验证不传 pack_id 时仍可创建 A1 默认会话。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 A2-Core 破坏旧 create_session 请求。
    """
    case_root = _make_case_root("session_no_pack")
    try:
        client = _client(case_root, monkeypatch)
        response = client.post(
            "/api/sessions",
            json={"request_id": "req_a2_no_pack_01", "character_id": "player_01"},
        )
        body = response.get_json()

        assert response.status_code == 201
        assert body["session_id"].startswith("sess_")
        assert "pack_id" not in body
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_create_session_rejects_missing_or_invalid_pack(monkeypatch: Any) -> None:
    """
    功能：验证未知 pack 和坏 persona_profile 会被拒绝，不写入坏会话。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 pack 参数边界退化。
    """
    case_root = _make_case_root("session_pack_invalid")
    try:
        client = _client(case_root, monkeypatch)
        missing = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_missing_pack_01",
                "character_id": "player_01",
                "pack_id": "missing_pack",
            },
        )
        bad_persona = client.post(
            "/api/sessions",
            json={
                "request_id": "req_a2_bad_persona_01",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
                "persona_profile": "bad",
            },
        )

        assert missing.status_code == 404
        assert missing.get_json()["error"]["code"] == "PACK_NOT_FOUND"
        assert bad_persona.status_code == 400
        assert bad_persona.get_json()["error"]["code"] == "INVALID_ARGUMENT"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
