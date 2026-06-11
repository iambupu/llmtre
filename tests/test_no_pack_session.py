"""
功能：覆盖 no pack session 的回归测试。
"""

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


class _NoPackRuntimeContext(ApiRuntimeContext):
    """
    功能：为 no-pack 会话测试提供真实 session_store 与可控 Story Pack registry。
    入参：db_path（str）：SQLite 路径；registry（StoryPackRegistry）：测试 registry。
    出参：_NoPackRuntimeContext。
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
    功能：创建 no-pack session 测试自管目录，避开 Windows tmp_path 权限噪声。
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


def _client_for_registry(registry: StoryPackRegistry, db_path: Path) -> Any:
    """
    功能：构造注册 sessions 蓝图的测试客户端，使用指定的 registry 和 DB。
    入参：registry（StoryPackRegistry）：测试 registry；db_path（Path）：SQLite 路径。
    出参：FlaskClient。
    异常：Flask 或 SQLite 初始化失败时向上抛出。
    """
    _init_runtime_db(db_path)
    context = _NoPackRuntimeContext(str(db_path), registry)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(sessions_blueprint)
    return app.test_client()


def test_session_without_pack_or_background_returns_error(monkeypatch: Any) -> None:
    """
    功能：验证既无 pack_id 也无 background 时，create_session 返回非法参数错误。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示无剧本兜底校验回归。
    """
    case_root = _make_case_root("nopack_no_bg")
    try:
        packs_root = case_root / "story_packs"
        (packs_root / "demo_a2_core").mkdir(parents=True)
        registry = StoryPackRegistry(packs_root)
        db_path = case_root / "runtime.db"
        client = _client_for_registry(registry, db_path)

        result = client.post(
            "/api/sessions",
            json={"request_id": "req_nopack_no_bg", "character_id": "player_01"},
        )
        body = result.get_json()
        assert result.status_code == 400
        assert body["error"]["code"] == "NO_STORY_PACK"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_session_with_background_generates_pack_and_creates_session(monkeypatch: Any) -> None:
    """
    功能：验证提供 background 时，StoryPackGenerationService 被调用，
          生成的 pack_id 用于创建会话并成功返回开场叙事。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示无剧本 LLM 生成链路或会话绑定回归。
    """
    case_root = _make_case_root("nopack_with_bg")
    try:
        packs_root = case_root / "story_packs"
        (packs_root / "demo_a2_core").mkdir(parents=True)
        registry = StoryPackRegistry(packs_root)
        db_path = case_root / "runtime.db"
        client = _client_for_registry(registry, db_path)

        monkeypatch.setattr(
            "web_api.blueprints.sessions.ensure_character_available",
            lambda _cid: True,
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.build_initial_turn_payload",
            lambda _cid, _sandbox_mode, **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2", "affordances": []},
                "final_response": "自定义背景开场叙事",
                "quick_actions": ["观察周围"],
                "affordances": [],
                "failure_reason": "",
                "suggested_next_step": "探索世界",
                "outcome": "initial_scene",
            },
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.get_play_state",
            lambda _cid, _sandbox_mode, recent_memory="", **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2"},
            },
        )

        generated_pack_id = "gen_pack_custom_background"

        def _generate_from_background(self: Any, background: str) -> dict[str, str]:
            """
            功能：提供 generate from background 测试辅助逻辑。
            入参：按函数签名接收 pytest fixture 或测试辅助参数。
            出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
            异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
            """
            return {"pack_id": generated_pack_id, "title": "自定义剧本"}

        monkeypatch.setattr(
            "web_api.blueprints.sessions.StoryPackGenerationService.generate_from_background",
            _generate_from_background,
        )
        # create_session 会调用 registry.refresh() 和 registry.get(pack_id)，
        # 需要 mock registry 让自动生成的 pack_id 能通过校验。
        monkeypatch.setattr(registry, "refresh", lambda: None)
        monkeypatch.setattr(
            registry,
            "get",
            lambda pid: (
                type(
                    "MockBundle",
                    (),
                    {
                        "summary": type(
                            "MockSummary",
                            (),
                            {
                                "pack_id": generated_pack_id,
                                "scenario_id": "default",
                                "title": "自定义剧本",
                                "version": "0.1.0",
                                "compiled_artifact_hash": "mock_hash",
                                "start_scene_id": "intro",
                            },
                        )(),
                        "quests": {},
                        "triggers": {},
                        "scenes": {},
                    },
                )()
                if pid == generated_pack_id
                else None
            ),
        )

        result = client.post(
            "/api/sessions",
            json={
                "request_id": "req_nopack_bg_01",
                "character_id": "player_01",
                "background": "一个蒸汽朋克城市中的冒险故事",
            },
        )
        body = result.get_json()
        assert result.status_code == 201
        assert "ok" in body
        assert body["pack_id"] == generated_pack_id
        assert body["scenario_id"] == "default"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_session_with_background_generation_fails_returns_fallback(monkeypatch: Any) -> None:
    """
    功能：当 StoryPackGenerationService 返回 error 时，
          create_session 应返回 SERVER_ERROR，避免坏包进入会话。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示生成失败降级路径回归。
    """
    case_root = _make_case_root("nopack_gen_fail")
    try:
        packs_root = case_root / "story_packs"
        (packs_root / "demo_a2_core").mkdir(parents=True)
        registry = StoryPackRegistry(packs_root)
        db_path = case_root / "runtime.db"
        client = _client_for_registry(registry, db_path)

        monkeypatch.setattr(
            "web_api.blueprints.sessions.ensure_character_available",
            lambda _cid: True,
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.build_initial_turn_payload",
            lambda _cid, _sandbox_mode, **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2", "affordances": []},
                "final_response": "降级开场叙事",
                "quick_actions": ["观察周围"],
                "affordances": [],
                "failure_reason": "",
                "suggested_next_step": "探索世界",
                "outcome": "initial_scene",
            },
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.get_play_state",
            lambda _cid, _sandbox_mode, recent_memory="", **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2"},
            },
        )

        def _generate_from_background(self: Any, background: str) -> dict[str, str]:
            """
            功能：提供 generate from background 测试辅助逻辑。
            入参：按函数签名接收 pytest fixture 或测试辅助参数。
            出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
            异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
            """
            return {"error": "LLM 不可用"}

        monkeypatch.setattr(
            "web_api.blueprints.sessions.StoryPackGenerationService.generate_from_background",
            _generate_from_background,
        )

        result = client.post(
            "/api/sessions",
            json={
                "request_id": "req_nopack_fail_01",
                "character_id": "player_01",
                "background": "任意背景",
            },
        )
        body = result.get_json()
        assert result.status_code == 500
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "LLM" in body["error"]["message"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_session_with_pack_ignores_background(monkeypatch: Any) -> None:
    """
    功能：同时提供 pack_id 和 background 时，background 应被忽略，
          会话按指定的 pack 创建（回归保护）。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 pack 优先语义被破坏。
    """
    case_root = _make_case_root("nopack_pack_first")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")
        registry = StoryPackRegistry(packs_root)
        db_path = case_root / "runtime.db"
        client = _client_for_registry(registry, db_path)

        monkeypatch.setattr(
            "web_api.blueprints.sessions.ensure_character_available",
            lambda _cid: True,
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.build_initial_turn_payload",
            lambda _cid, _sandbox_mode, **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2", "affordances": []},
                "final_response": "demo 剧本开场",
                "quick_actions": ["观察周围"],
                "affordances": [],
                "failure_reason": "",
                "suggested_next_step": "探索世界",
                "outcome": "initial_scene",
            },
        )
        monkeypatch.setattr(
            "web_api.blueprints.sessions.get_play_state",
            lambda _cid, _sandbox_mode, recent_memory="", **_kwargs: {
                "active_character": {"id": "player_01"},
                "scene_snapshot": {"schema_version": "scene_snapshot.v2"},
            },
        )

        generate_called = False

        def _tracked_generate(self: Any, _bg: str) -> dict[str, str]:
            """
            功能：提供 tracked generate 测试辅助逻辑。
            入参：按函数签名接收 pytest fixture 或测试辅助参数。
            出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
            异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
            """
            nonlocal generate_called
            generate_called = True
            return {"pack_id": "gen_should_not_happen", "title": "不应被调用"}

        monkeypatch.setattr(
            "web_api.blueprints.sessions.StoryPackGenerationService.generate_from_background",
            _tracked_generate,
        )

        result = client.post(
            "/api/sessions",
            json={
                "request_id": "req_nopack_pack_first",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
                "background": "这段背景应该被忽略",
            },
        )
        body = result.get_json()
        assert result.status_code == 201
        assert body["pack_id"] == "demo_a2_core"
        assert not generate_called
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
