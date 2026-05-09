from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from flask import Flask

from tools.packs.registry import StoryPackRegistry
from web_api.blueprints.story_packs import story_packs_blueprint
from web_api.service import ApiRuntimeContext


def _make_case_root(name: str) -> Path:
    """
    功能：创建 Web API A2 测试自管目录，避开 Windows tmp_path 权限噪声。
    入参：name（str）：用例名前缀。
    出参：Path，已创建目录。
    异常：目录创建失败时向上抛出。
    """
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _copy_demo_pack(target_root: Path) -> None:
    """
    功能：复制官方 demo pack 到测试 registry 根目录。
    入参：target_root（Path）：临时 story_packs 根目录。
    出参：None。
    异常：复制失败时向上抛出。
    """
    shutil.copytree("story_packs/demo_a2_core", target_root / "demo_a2_core")


def _client_for_registry(registry: StoryPackRegistry) -> Any:
    """
    功能：构造只注册 story_packs 蓝图的测试客户端。
    入参：registry（StoryPackRegistry）：待注入的 registry。
    出参：FlaskClient。
    异常：Flask 初始化失败时向上抛出。
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    context = ApiRuntimeContext()
    context.story_pack_registry = registry
    app.extensions["tre_api_context"] = context
    app.register_blueprint(story_packs_blueprint)
    return app.test_client()


def test_list_story_packs_returns_valid_demo_and_diagnostics() -> None:
    """
    功能：验证 pack 列表 API 只返回合法 demo pack，并保留坏包诊断。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示 registry/API 列表契约回归。
    """
    case_root = _make_case_root("story_pack_api")
    try:
        _copy_demo_pack(case_root)
        (case_root / "bad_pack").mkdir()
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)

        response = client.get("/api/story-packs")
        body = response.get_json()

        assert response.status_code == 200
        assert body["packs"][0]["pack_id"] == "demo_a2_core"
        assert body["packs"][0]["scene_count"] == 3
        assert "bad_pack" in body["diagnostics"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_get_story_pack_returns_scene_preview_and_rejects_missing() -> None:
    """
    功能：验证 pack 详情 API 返回 manifest 与 scenes，并对缺失 pack 返回 PACK_NOT_FOUND。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示详情或缺失分支契约回归。
    """
    case_root = _make_case_root("story_pack_detail")
    try:
        _copy_demo_pack(case_root)
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)

        found = client.get("/api/story-packs/demo_a2_core")
        missing = client.get("/api/story-packs/missing_pack")

        found_body = found.get_json()
        missing_body = missing.get_json()
        assert found.status_code == 200
        assert found_body["summary"]["pack_id"] == "demo_a2_core"
        assert len(found_body["scenes"]) == 3
        assert missing.status_code == 404
        assert missing_body["error"]["code"] == "PACK_NOT_FOUND"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
