"""
功能：覆盖 story packs a2 的回归测试。
"""

from __future__ import annotations

import base64
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml
from flask import Flask

import tools.packs.release_manager as release_manager
from tools.packs.registry import StoryPackRegistry
from web_api.blueprints.story_packs import story_packs_blueprint
from web_api.service import ApiRuntimeContext

TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_DATA_URL.split(",", 1)[1])
TINY_MP4_BYTES = b"mp4"
TINY_MP4_DATA_URL = "data:video/mp4;base64," + base64.b64encode(TINY_MP4_BYTES).decode("ascii")
TINY_MP3_BYTES = b"mp3"
TINY_MP3_DATA_URL = "data:audio/mpeg;base64," + base64.b64encode(TINY_MP3_BYTES).decode("ascii")


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
    功能：复制外部示例 demo pack 到测试 registry 根目录。
    入参：target_root（Path）：临时 story_packs 根目录。
    出参：None。
    异常：复制失败时向上抛出。
    """
    shutil.copytree("examples/story_packs/demo_a2_core", target_root / "demo_a2_core")


def _release_import_payload(pack_id: str = "release_pack") -> dict[str, Any]:
    """
    功能：构造 A2-Release 导入接口使用的最小合法 pack payload。
    入参：pack_id（str，默认 release_pack）：导入目标 pack_id。
    出参：dict[str, Any]，包含 manifest、scenes 与 lore。
    异常：无。
    """
    return {
        "manifest": {
            "pack_id": pack_id,
            "version": "0.1.0",
            "title": "Release 导入包",
            "scenario_id": "default",
            "start_scene_id": "release_start",
            "supported_actions": ["observe", "inspect"],
            "lore_files": ["world.md"],
        },
        "scenes": {
            "release_start": {
                "scene_id": "release_start",
                "display_name": "发布测试入口",
                "summary": "用于验证 A2-Release 导入链路的起点。",
                "exits": [],
                "interactables": [
                    {
                        "interaction_id": "inspect_release_marker",
                        "label": "检查发布标记",
                        "kind": "inspect",
                        "target_ref": "release_marker",
                    }
                ],
                "visible_npcs": ["release_keeper"],
                "visible_items": ["release_marker"],
            }
        },
        "lore": {"world.md": "# Release 导入包\n\n用于 API 导入验收。"},
    }


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


class _OldFieldGeneratedPack:
    """模拟历史 LLM prompt 产出的旧字段场景。"""

    def generate(self, prompt: str = "", player_background: str = "") -> dict[str, Any]:
        """
        功能：返回使用 name/description/npcs/objects 旧字段的最小可注册剧本包。
        入参：prompt（str，默认空）：生成 prompt，本测试不使用；
            player_background（str，默认空）：背景文本。
        出参：dict[str, Any]，包含 manifest 与 lore。
        异常：无。
        """
        return {
            "manifest": {
                "pack_id": "generated_old_fields",
                "version": "0.1.0",
                "title": "旧字段生成包",
                "author": "TRE Test",
                "description": "测试旧字段映射。",
                "scenario_id": "generated",
                "start_scene_id": "old_start",
                "supported_actions": ["observe", "inspect"],
                "lore_files": ["world.md"],
                "rules_overlay": {},
                "scenes": {
                    "old_start": {
                        "name": "旧字段入口",
                        "description": "旧字段描述会被映射为 summary。",
                        "exits": [],
                        "interactables": [],
                        "npcs": ["npc_old"],
                        "objects": ["item_old"],
                    }
                },
            },
            "lore": {"world.md": f"# 测试世界\n\n{player_background}"},
        }


class _InvalidGeneratedPack:
    """模拟生成后无法通过 registry 复验的剧本包。"""

    def generate(self, prompt: str = "", player_background: str = "") -> dict[str, Any]:
        """
        功能：返回 start_scene_id 与实际场景不一致的无效剧本包。
        入参：prompt（str，默认空）：生成 prompt，本测试不使用；
            player_background（str，默认空）：背景文本。
        出参：dict[str, Any]，包含 manifest 与 lore。
        异常：无。
        """
        return {
            "manifest": {
                "pack_id": "generated_invalid",
                "version": "0.1.0",
                "title": "无效生成包",
                "author": "TRE Test",
                "description": "测试 registry 复验失败路径。",
                "scenario_id": "generated",
                "start_scene_id": "missing_start",
                "supported_actions": ["observe"],
                "lore_files": ["world.md"],
                "rules_overlay": {},
                "scenes": {
                    "other_scene": {
                        "display_name": "另一个场景",
                        "summary": "实际写入的场景与 start_scene_id 不一致。",
                        "exits": [],
                        "interactables": [],
                        "visible_npcs": [],
                        "visible_items": [],
                    }
                },
            },
            "lore": {"world.md": f"# 测试世界\n\n{player_background}"},
        }


class _EscapingGeneratedPack:
    """模拟生成器产出路径逃逸文件名的剧本包。"""

    def generate(self, prompt: str = "", player_background: str = "") -> dict[str, Any]:
        """
        功能：返回带路径逃逸 scene_id 与 lore 文件名的生成结果。
        入参：prompt（str，默认空）：生成 prompt，本测试不使用；
            player_background（str，默认空）：背景文本。
        出参：dict[str, Any]，包含 manifest 与 lore。
        异常：无。
        """
        return {
            "manifest": {
                "pack_id": "generated_escape",
                "version": "0.1.0",
                "title": "路径逃逸生成包",
                "scenario_id": "generated",
                "start_scene_id": "../outside_scene",
                "supported_actions": ["observe"],
                "lore_files": ["../outside.md"],
                "scenes": {
                    "../outside_scene": {
                        "display_name": "越界场景",
                        "summary": "该场景 ID 不应参与文件写入。",
                        "exits": [],
                        "interactables": [],
                        "visible_npcs": [],
                        "visible_items": [],
                    }
                },
            },
            "lore": {"../outside.md": f"# 越界\n\n{player_background}"},
        }


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
        assert body["packs"][0]["start_scene_title"]
        assert body["packs"][0]["start_scene_title"] != body["packs"][0]["start_scene_id"]
        assert "bad_pack" in body["diagnostics"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_openapi_story_pack_summary_declares_player_facing_fields_required() -> None:
    """
    功能：验证 OpenAPI StoryPackSummary 把玩家展示名与 A2-Plus 计数字段声明为必填，
        避免前端类型漂移。
    入参：无。
    出参：None。
    异常：断言失败表示 OpenAPI 与 Python/TypeScript 摘要契约不同步。
    """
    spec = yaml.safe_load(Path("config/api/openapi.yaml").read_text(encoding="utf-8"))
    required = set(spec["components"]["schemas"]["StoryPackSummary"]["required"])

    assert {"start_scene_title", "quest_count", "trigger_count", "asset_count"} <= required


def test_openapi_error_codes_declare_asset_not_found() -> None:
    """
    功能：验证 Story Pack 媒体路由使用的 ASSET_NOT_FOUND 已进入 OpenAPI 错误码枚举。
    入参：无。
    出参：None。
    异常：断言失败表示 Web API 实际错误码与 OpenAPI 契约漂移。
    """
    spec = yaml.safe_load(Path("config/api/openapi.yaml").read_text(encoding="utf-8"))
    error_codes = set(spec["components"]["schemas"]["ErrorObject"]["properties"]["code"]["enum"])

    assert "ASSET_NOT_FOUND" in error_codes


def test_openapi_story_pack_asset_declares_multimedia_mime_types() -> None:
    """
    功能：验证 Story Pack asset 路由在 OpenAPI 中声明图片、GIF、视频和音频 MIME。
    入参：无。
    出参：None。
    异常：断言失败表示 Story Pack 多媒体服务能力与 API 契约漂移。
    """
    spec = yaml.safe_load(Path("config/api/openapi.yaml").read_text(encoding="utf-8"))
    content = set(
        spec["paths"]["/api/story-packs/{pack_id}/assets/{asset_path}"]["get"]["responses"]["200"][
            "content"
        ]
    )

    assert {"image/gif", "video/mp4", "audio/mpeg", "audio/flac"} <= content


def test_import_story_pack_registers_uploaded_payload() -> None:
    """
    功能：验证 A2-Release 导入 API 会写入上传 payload，并通过 registry 复验后进入列表/详情。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示上传导入或预览链路回归。
    """
    case_root = _make_case_root("story_pack_import_valid")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)

        response = client.post("/api/story-packs", json=_release_import_payload())
        body = response.get_json()
        listed = client.get("/api/story-packs").get_json()
        detail = client.get("/api/story-packs/release_pack").get_json()

        assert response.status_code == 201
        assert body["summary"]["pack_id"] == "release_pack"
        assert body["summary"]["start_scene_title"] == "发布测试入口"
        assert (case_root / "release_pack" / "manifest.json").exists()
        assert [item["pack_id"] for item in listed["packs"]] == ["release_pack"]
        assert detail["summary"]["pack_id"] == "release_pack"
        assert detail["summary"]["start_scene_title"] == "发布测试入口"
        assert detail["scenes"][0]["visible_npcs"] == ["release_keeper"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_uploads_and_serves_multimedia_assets() -> None:
    """
    功能：验证导入 API 可通过 asset_files 上传图片/视频/音频物料，并由只读路由返回。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示多媒体导入、manifest 复验或 asset 文件路由回归。
    """
    case_root = _make_case_root("story_pack_import_assets")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("asset_pack")
        payload["manifest"]["assets"] = {
            "release_bg": {
                "kind": "background",
                "src": "backgrounds/release.png",
                "alt": "发布入口背景",
            },
            "release_intro": {
                "kind": "illustration",
                "media_type": "video",
                "src": "video/intro.mp4",
                "playback": {
                    "mode": "loop",
                    "controls": False,
                    "muted": True,
                    "preload": "auto",
                    "volume": 0.35,
                    "start_time_seconds": 0.5,
                    "end_time_seconds": 4.0,
                },
            },
            "release_theme": {
                "kind": "ui",
                "media_type": "audio",
                "src": "audio/theme.mp3",
                "playback": {
                    "mode": "once",
                    "controls": True,
                    "preload": "metadata",
                },
            },
        }
        payload["scenes"]["release_start"]["background_asset_id"] = "release_bg"
        payload["scenes"]["release_start"]["image_asset_id"] = "release_intro"
        payload["asset_files"] = {
            "backgrounds/release.png": TINY_PNG_DATA_URL,
            "video/intro.mp4": TINY_MP4_DATA_URL,
            "audio/theme.mp3": TINY_MP3_DATA_URL,
        }

        response = client.post("/api/story-packs", json=payload)
        detail = client.get("/api/story-packs/asset_pack").get_json()
        asset_response = client.get("/api/story-packs/asset_pack/assets/backgrounds/release.png")
        video_response = client.get("/api/story-packs/asset_pack/assets/video/intro.mp4")
        audio_response = client.get("/api/story-packs/asset_pack/assets/audio/theme.mp3")
        undeclared_response = client.get(
            "/api/story-packs/asset_pack/assets/backgrounds/missing.png"
        )

        assert response.status_code == 201
        assert response.get_json()["summary"]["asset_count"] == 3
        assert detail["manifest"]["assets"]["release_bg"]["src"] == "backgrounds/release.png"
        assert detail["manifest"]["assets"]["release_intro"]["playback"]["mode"] == "loop"
        assert detail["manifest"]["assets"]["release_intro"]["playback"]["muted"] is True
        assert detail["scenes"][0]["background_asset_id"] == "release_bg"
        assert asset_response.status_code == 200
        assert asset_response.get_data() == TINY_PNG_BYTES
        assert video_response.status_code == 200
        assert video_response.get_data() == TINY_MP4_BYTES
        assert video_response.mimetype == "video/mp4"
        assert audio_response.status_code == 200
        assert audio_response.get_data() == TINY_MP3_BYTES
        assert audio_response.mimetype == "audio/mpeg"
        assert undeclared_response.status_code == 404
        assert undeclared_response.get_json()["error"]["code"] == "ASSET_NOT_FOUND"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_story_pack_asset_route_uses_cached_registry_without_refresh(monkeypatch: Any) -> None:
    """
    功能：验证 Story Pack 媒体文件路由只读 registry 缓存，不为每个文件请求重复 refresh。
    入参：monkeypatch（Any）：pytest monkeypatch，用于拦截 registry.refresh。
    出参：None。
    异常：断言失败表示浏览器媒体请求仍可能触发全量 pack 扫描与哈希。
    """
    case_root = _make_case_root("story_pack_asset_cached")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("asset_pack")
        payload["manifest"]["assets"] = {
            "release_bg": {
                "kind": "background",
                "src": "backgrounds/release.png",
                "alt": "发布入口背景",
            }
        }
        payload["asset_files"] = {"backgrounds/release.png": TINY_PNG_DATA_URL}
        imported = client.post("/api/story-packs", json=payload)
        assert imported.status_code == 201

        def fail_refresh() -> None:
            """
            功能：在 asset 路由误触发 registry.refresh 时让测试失败。
            入参：无。
            出参：None。
            异常：AssertionError；表示只读媒体请求触发了不应发生的刷新。
            """
            raise AssertionError("asset 路由不应刷新 StoryPackRegistry")

        monkeypatch.setattr(registry, "refresh", fail_refresh)

        asset_response = client.get("/api/story-packs/asset_pack/assets/backgrounds/release.png")

        assert asset_response.status_code == 200
        assert asset_response.get_data() == TINY_PNG_BYTES
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_oversized_asset_before_decode(monkeypatch: Any) -> None:
    """
    功能：验证超出大小上限的 asset_files 会在 base64 解码前被拒绝。
    入参：monkeypatch（Any）：pytest monkeypatch，用于缩小大小上限并拦截 base64 解码。
    出参：None。
    异常：断言失败表示超大媒体上传仍可能先解码占用内存。
    """
    case_root = _make_case_root("story_pack_import_oversized_asset")
    decode_called = False

    def fail_b64decode(_content: str, validate: bool = False) -> bytes:
        """
        功能：标记测试中是否进入真实 base64 解码阶段。
        入参：_content（str）：待解码文本；validate（bool，默认 False）：标准库兼容参数。
        出参：bytes；本测试不应返回。
        异常：AssertionError；表示大小预检未能阻止解码。
        """
        nonlocal decode_called
        decode_called = True
        raise AssertionError("超限 asset_files 不应进入 base64 解码")

    try:
        monkeypatch.setattr(release_manager, "MAX_ASSET_FILE_BYTES", 8)
        monkeypatch.setattr(release_manager.base64, "b64decode", fail_b64decode)
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("oversized_asset_pack")
        payload["manifest"]["assets"] = {
            "huge_bg": {
                "kind": "background",
                "src": "backgrounds/huge.png",
                "alt": "超限背景",
            }
        }
        payload["asset_files"] = {"backgrounds/huge.png": "A" * 16}

        response = client.post("/api/story-packs", json=payload)
        body = response.get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert "超过 8 字节限制" in body["error"]["message"]
        assert decode_called is False
        assert not (case_root / "oversized_asset_pack").exists()
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_undeclared_asset_files_without_residue() -> None:
    """
    功能：验证导入 API 拒绝未在 manifest.assets 声明的 asset_files，避免孤儿媒体进入包目录。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示导入边界或失败清理回归。
    """
    case_root = _make_case_root("story_pack_import_orphan_asset")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("orphan_asset_pack")
        payload["manifest"]["assets"] = {
            "release_bg": {
                "kind": "background",
                "src": "backgrounds/release.png",
                "alt": "发布入口背景",
            }
        }
        payload["asset_files"] = {"backgrounds/orphan.png": TINY_PNG_DATA_URL}

        response = client.post("/api/story-packs", json=payload)
        body = response.get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert "未在 manifest.assets 中声明" in body["error"]["message"]
        assert not (case_root / "orphan_asset_pack").exists()
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_invalid_payload_and_cleans_directory() -> None:
    """
    功能：验证导入坏包时返回诊断，并清理本次创建目录，避免污染可用 registry。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示坏包可能残留或被误列为可用。
    """
    case_root = _make_case_root("story_pack_import_invalid")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("release_bad")
        payload["manifest"]["start_scene_id"] = "missing_scene"

        response = client.post("/api/story-packs", json=payload)
        body = response.get_json()
        listed = client.get("/api/story-packs").get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "PACK_IMPORT_FAILED"
        assert not (case_root / "release_bad").exists()
        assert listed["packs"] == []
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_pack_id_path_escape_without_residue() -> None:
    """
    功能：验证导入接口拒绝带路径分隔符的 pack_id，并且不会创建父目录残留。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示 pack_id 稳定 ID 准入或失败清理回归。
    """
    case_root = _make_case_root("story_pack_import_bad_pack_id")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)

        response = client.post("/api/story-packs", json=_release_import_payload("nested/bad"))
        body = response.get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert not any(case_root.iterdir())
        registry.refresh()
        assert registry.diagnostics() == {}
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_json_collection_path_escape_without_residue() -> None:
    """
    功能：验证上传 scenes/quests/triggers 的对象 ID 不能通过相对路径越出集合目录。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示 JSON 集合文件 stem 准入或失败清理回归。
    """
    case_root = _make_case_root("story_pack_import_bad_scene_id")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("release_escape")
        payload["scenes"]["release_start"]["scene_id"] = "../outside_scene"

        response = client.post("/api/story-packs", json=payload)
        body = response.get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert not any(case_root.iterdir())
        registry.refresh()
        assert registry.diagnostics() == {}
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_import_story_pack_rejects_lore_path_escape_without_residue() -> None:
    """
    功能：验证上传 lore 文件名不能使用相对路径、绝对路径或盘符逃逸。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示 lore 相对路径准入或失败清理回归。
    """
    case_root = _make_case_root("story_pack_import_bad_lore_path")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        payload = _release_import_payload("release_bad_lore")
        payload["lore"] = {"../outside.md": "越界 lore 不应写入"}

        response = client.post("/api/story-packs", json=payload)
        body = response.get_json()

        assert response.status_code == 400
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert not any(case_root.iterdir())
        registry.refresh()
        assert registry.diagnostics() == {}
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_delete_story_pack_removes_any_local_pack() -> None:
    """
    功能：验证 A2-Release 删除 API 可以删除任一本地 pack，不再内置保护示例内容。
    入参：无，使用 test_runs 自管目录。
    出参：None。
    异常：断言失败表示删除边界或官方 pack 保护失效。
    """
    case_root = _make_case_root("story_pack_delete")
    try:
        _copy_demo_pack(case_root)
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        imported = client.post("/api/story-packs", json=_release_import_payload())
        deleted = client.delete("/api/story-packs/release_pack")
        deleted_demo = client.delete("/api/story-packs/demo_a2_core")
        listed = client.get("/api/story-packs").get_json()

        assert imported.status_code == 201
        assert deleted.status_code == 200
        assert deleted.get_json()["deleted_pack_id"] == "release_pack"
        assert not (case_root / "release_pack").exists()
        assert deleted_demo.status_code == 200
        assert deleted_demo.get_json()["deleted_pack_id"] == "demo_a2_core"
        assert not (case_root / "demo_a2_core").exists()
        assert listed["packs"] == []
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_generate_story_pack_registers_valid_pack_and_maps_old_scene_fields(
    monkeypatch: Any,
) -> None:
    """
    功能：验证生成接口能把旧字段场景写成新版契约，并且只在 registry 可见后返回成功。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换生成器。
    出参：None。
    异常：断言失败表示生成包注册或字段映射回归。
    """
    case_root = _make_case_root("story_pack_generate_valid")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        monkeypatch.setattr(
            "tools.packs.generation_service.StoryPackGenerator",
            _OldFieldGeneratedPack,
        )

        response = client.post("/api/story-packs/generate", json={"background": "古城冒险"})
        body = response.get_json()

        assert response.status_code == 200
        pack_id = body["pack_id"]
        manifest = json.loads((case_root / pack_id / "manifest.json").read_text(encoding="utf-8"))
        registry.refresh()
        bundle = registry.get(pack_id)
        assert bundle is not None
        assert "scenes" not in manifest
        assert manifest["source_background_hash"] == body["content_hash"]
        assert bundle.summary.source_background_hash == body["content_hash"]
        scene = bundle.scenes["old_start"]
        assert scene.display_name == "旧字段入口"
        assert scene.summary == "旧字段描述会被映射为 summary。"
        assert scene.visible_npcs == ["npc_old"]
        assert scene.visible_items == ["item_old"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_generate_story_pack_deduplicates_same_background_by_source_hash(
    monkeypatch: Any,
) -> None:
    """
    功能：验证相同 background 二次生成时按来源 hash 命中同一个 Story Pack。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换生成器。
    出参：None。
    异常：断言失败表示生成包去重键或 registry 摘要字段回归。
    """
    case_root = _make_case_root("story_pack_generate_dedupe")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        monkeypatch.setattr(
            "tools.packs.generation_service.StoryPackGenerator",
            _OldFieldGeneratedPack,
        )

        first = client.post("/api/story-packs/generate", json={"background": "古城冒险"})
        first_body = first.get_json()
        second = client.post("/api/story-packs/generate", json={"background": "古城冒险"})
        second_body = second.get_json()

        assert first.status_code == 200
        assert second.status_code == 200
        assert second_body["duplicate"] is True
        assert second_body["pack_id"] == first_body["pack_id"]
        assert second_body["content_hash"] == first_body["content_hash"]
        assert len([path for path in case_root.iterdir() if path.is_dir()]) == 1
        registry.refresh()
        summary = registry.list_summaries()[0]
        assert summary["source_background_hash"] == first_body["content_hash"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_generate_story_pack_rejects_invalid_generated_pack_and_cleans_directory(
    monkeypatch: Any,
) -> None:
    """
    功能：验证生成内容未通过 registry 复验时接口失败，并移除本次写入的坏包目录。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换生成器。
    出参：None。
    异常：断言失败表示坏包可能污染 story_packs 目录或生成接口误报成功。
    """
    case_root = _make_case_root("story_pack_generate_invalid")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        monkeypatch.setattr(
            "tools.packs.generation_service.StoryPackGenerator",
            _InvalidGeneratedPack,
        )

        response = client.post("/api/story-packs/generate", json={"background": "错位场景"})
        body = response.get_json()

        assert response.status_code == 500
        assert body["error"]["message"] == "生成的剧本包未通过校验"
        assert not any(case_root.iterdir())
        registry.refresh()
        assert registry.list_summaries() == []
        assert registry.diagnostics() == {}
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_generate_story_pack_rejects_path_escape_and_cleans_directory(
    monkeypatch: Any,
) -> None:
    """
    功能：验证 background 生成产物含路径逃逸文件名时失败，并清理本次生成目录。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换生成器。
    出参：None。
    异常：断言失败表示生成服务写盘边界或失败清理回归。
    """
    case_root = _make_case_root("story_pack_generate_escape")
    try:
        registry = StoryPackRegistry(case_root)
        client = _client_for_registry(registry)
        monkeypatch.setattr(
            "tools.packs.generation_service.StoryPackGenerator",
            _EscapingGeneratedPack,
        )

        response = client.post("/api/story-packs/generate", json={"background": "越界背景"})
        body = response.get_json()

        assert response.status_code == 500
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "路径非法" in body["error"]["message"]
        assert not any(case_root.iterdir())
        registry.refresh()
        assert registry.list_summaries() == []
        assert registry.diagnostics() == {}
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
        assert found_body["summary"]["start_scene_title"]
        assert found_body["summary"]["start_scene_title"] != found_body["summary"]["start_scene_id"]
        assert len(found_body["scenes"]) == 3
        assert missing.status_code == 404
        assert missing_body["error"]["code"] == "PACK_NOT_FOUND"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
