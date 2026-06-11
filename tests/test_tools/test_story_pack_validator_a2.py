"""
功能：覆盖 story pack validator a2 的回归测试。
"""

from __future__ import annotations

import base64
import json
import shutil
import uuid
from pathlib import Path

import pytest

from examples.demo_playthrough import build_demo_playthrough
from tools.packs.registry import (
    StoryPackRegistry,
    StoryPackValidationError,
    validate_story_pack,
)

TINY_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _make_case_root(name: str) -> Path:
    """
    功能：在仓库 test_runs 下创建当前测试专用目录，避开 Windows tmp_path 权限噪声。
    入参：name（str）：测试用例名前缀。
    出参：Path，已创建的空目录。
    异常：目录创建或清理失败时向上抛出，说明本地测试工作区不可写。
    """
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _remove_case_root(root: Path) -> None:
    """
    功能：清理当前测试自管目录。
    入参：root（Path）：由 _make_case_root 创建的目录。
    出参：None。
    异常：清理失败时向上抛出，避免残留目录掩盖测试污染。
    """
    if root.exists():
        shutil.rmtree(root)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """
    功能：写入测试用 JSON 文件。
    入参：path（Path）：目标路径；payload（dict[str, object]）：JSON 对象。
    出参：None。
    异常：文件系统写入失败时向上抛出。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_minimal_pack(root: Path, target_scene_id: str = "start") -> None:
    """
    功能：构造最小合法或可控非法的 Story Pack。
    入参：root（Path）：pack 根目录；target_scene_id（str，默认 start）：出口目标。
    出参：None。
    异常：文件系统写入失败时向上抛出。
    """
    _write_json(
        root / "manifest.json",
        {
            "pack_id": root.name,
            "version": "0.1.0",
            "title": "测试包",
            "start_scene_id": "start",
            "supported_actions": ["observe"],
            "lore_files": [],
        },
    )
    _write_json(
        root / "scenes" / "start.json",
        {
            "scene_id": "start",
            "display_name": "起点",
            "summary": "测试场景",
            "exits": [{"target_scene_id": target_scene_id, "label": "继续"}],
            "interactables": [
                {
                    "interaction_id": "inspect_start",
                    "label": "观察起点",
                    "kind": "observe",
                    "target_ref": "start",
                }
            ],
        },
    )


def test_validate_demo_story_pack_success() -> None:
    """
    功能：验证官方 A2-Core/A2-Plus demo pack 可被校验器接受，并满足内容规模验收。
    入参：无。
    出参：None。
    异常：断言失败表示 demo pack 或 v0 契约漂移。
    """
    bundle = validate_story_pack("examples/story_packs/demo_a2_core")
    npc_ids = {npc_id for scene in bundle.scenes.values() for npc_id in scene.visible_npcs}

    assert bundle.summary.pack_id == "demo_a2_core"
    assert bundle.summary.start_scene_title
    assert bundle.summary.start_scene_title != bundle.summary.start_scene_id
    assert bundle.summary.scene_count >= 3
    assert bundle.summary.interaction_count >= 2
    # 验收意图：把路线图里“至少 2 个 NPC”固化为 demo pack 的回归条件。
    assert len(npc_ids) >= 2
    assert bundle.summary.compiled_artifact_hash


def test_demo_playthrough_script_uses_valid_demo_pack() -> None:
    """
    功能：验证 A2-Release 演示脚本基于官方 demo pack 生成固定试玩步骤。
    入参：无。
    出参：None。
    异常：断言失败表示演示脚本未覆盖官方 pack 或关键 A2Plus 交互。
    """
    payload = build_demo_playthrough()

    assert payload["pack_id"] == "demo_a2_core"
    assert payload["scene_count"] >= 3
    assert payload["quest_count"] >= 1
    assert payload["trigger_count"] >= 1
    assert "翻看火坑灰烬" in payload["steps"]
    assert "询问石门旁的守门学者" in payload["steps"]


def test_compiled_hash_includes_triggers_and_quests() -> None:
    """
    功能：验证 trigger/quest 文件内容变化会改变 Story Pack compiled hash。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：断言失败表示 pack 身份摘要未覆盖 A2-Plus 内容。
    """
    case_root = _make_case_root("hash_a2plus")
    try:
        pack_root = case_root / "demo_a2_core"
        shutil.copytree("examples/story_packs/demo_a2_core", pack_root)
        first = validate_story_pack(pack_root).summary.compiled_artifact_hash

        trigger_path = pack_root / "triggers" / "inspect_camp_firepit.json"
        trigger_payload = json.loads(trigger_path.read_text(encoding="utf-8"))
        trigger_payload["description"] += " hash-change"
        trigger_path.write_text(
            json.dumps(trigger_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        second = validate_story_pack(pack_root).summary.compiled_artifact_hash
        assert second != first

        quest_path = pack_root / "quests" / "find_the_key.json"
        quest_payload = json.loads(quest_path.read_text(encoding="utf-8"))
        quest_payload["description"] += " hash-change"
        quest_path.write_text(
            json.dumps(quest_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        third = validate_story_pack(pack_root).summary.compiled_artifact_hash
        assert third != second
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_accepts_multimedia_assets_and_hashes_file_content() -> None:
    """
    功能：验证 Story Pack 可声明图片/GIF/视频/音频物料，场景/NPC/物品可引用且内容进入 hash。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：断言失败表示多媒体物料契约、引用校验或 hash 覆盖退化。
    """
    case_root = _make_case_root("pack_assets")
    try:
        pack_root = case_root / "pack_assets"
        _write_minimal_pack(pack_root)
        manifest_path = pack_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"] = {
            "start_bg": {
                "kind": "background",
                "media_type": "gif",
                "src": "backgrounds/start.gif",
                "alt": "起点背景",
            },
            "intro_video": {
                "kind": "illustration",
                "media_type": "video",
                "src": "video/intro.mp4",
                "playback": {
                    "mode": "once",
                    "controls": False,
                    "muted": True,
                    "preload": "auto",
                    "volume": 0.4,
                    "start_time_seconds": 1.0,
                    "end_time_seconds": 3.0,
                },
            },
            "theme_audio": {
                "kind": "ui",
                "media_type": "audio",
                "src": "audio/theme.mp3",
                "playback": {
                    "mode": "loop",
                    "controls": True,
                    "preload": "metadata",
                    "volume": 0.8,
                },
            },
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        scene_path = pack_root / "scenes" / "start.json"
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        scene["background_asset_id"] = "start_bg"
        scene["image_asset_id"] = "intro_video"
        scene["visible_npcs"] = [
            {"id": "keeper", "label": "守门人", "portrait_asset_id": "start_bg"}
        ]
        scene["visible_items"] = [
            {"id": "notice", "label": "告示牌", "icon_asset_id": "theme_audio"}
        ]
        scene_path.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
        gif_path = pack_root / "assets" / "backgrounds" / "start.gif"
        video_path = pack_root / "assets" / "video" / "intro.mp4"
        audio_path = pack_root / "assets" / "audio" / "theme.mp3"
        for path, content in (
            (gif_path, b"gif"),
            (video_path, b"video"),
            (audio_path, b"audio"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        first = validate_story_pack(pack_root)
        manifest["assets"]["intro_video"]["playback"]["mode"] = "loop"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        second = validate_story_pack(pack_root)
        video_path.write_bytes(b"video-changed")
        third = validate_story_pack(pack_root)

        assert first.summary.asset_count == 3
        assert first.manifest.assets["intro_video"].playback is not None
        assert first.manifest.assets["intro_video"].playback.mode == "once"
        assert first.scenes["start"].background_asset_id == "start_bg"
        assert first.summary.compiled_artifact_hash != second.summary.compiled_artifact_hash
        assert second.summary.compiled_artifact_hash != third.summary.compiled_artifact_hash
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_invalid_asset_playback_window() -> None:
    """
    功能：验证 Story Pack asset.playback 的播放窗口必须合法，避免运行时收到不可执行策略。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示播放生命周期契约校验失效。
    """
    case_root = _make_case_root("pack_asset_bad_playback")
    try:
        pack_root = case_root / "pack_asset_bad_playback"
        _write_minimal_pack(pack_root)
        manifest_path = pack_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"] = {
            "intro_video": {
                "kind": "illustration",
                "media_type": "video",
                "src": "video/intro.mp4",
                "playback": {
                    "mode": "once",
                    "start_time_seconds": 5,
                    "end_time_seconds": 5,
                },
            }
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        video_path = pack_root / "assets" / "video" / "intro.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        diagnostics = ";".join(exc_info.value.diagnostics)
        assert "playback" in diagnostics
        assert "end_time_seconds" in diagnostics
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_undeclared_image_asset_reference() -> None:
    """
    功能：验证场景图片引用必须指向 manifest.assets 中已声明的 asset。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示图片引用完整性校验失效。
    """
    case_root = _make_case_root("pack_asset_bad_ref")
    try:
        pack_root = case_root / "pack_asset_bad_ref"
        _write_minimal_pack(pack_root)
        scene_path = pack_root / "scenes" / "start.json"
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        scene["background_asset_id"] = "missing_asset"
        scene_path.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "引用未声明 asset" in ";".join(exc_info.value.diagnostics)
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_missing_manifest() -> None:
    """
    功能：验证缺少 manifest.json 时返回明确诊断。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示校验边界退化。
    """
    case_root = _make_case_root("missing_manifest")
    try:
        pack_root = case_root / "missing_manifest"
        pack_root.mkdir()

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "缺少 manifest.json" in exc_info.value.diagnostics[0]
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_missing_start_scene() -> None:
    """
    功能：验证 manifest.start_scene_id 必须引用已有 scene。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示起始场景引用校验失效。
    """
    case_root = _make_case_root("bad_start")
    try:
        pack_root = case_root / "bad_start"
        _write_minimal_pack(pack_root)
        manifest_path = pack_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["start_scene_id"] = "missing"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "start_scene_id 不存在" in exc_info.value.diagnostics[0]
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_manifest_pack_id_mismatch() -> None:
    """
    功能：验证 manifest.pack_id 必须与目录名一致，避免内容身份与 registry key 分裂。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示 pack 身份准入校验失效。
    """
    case_root = _make_case_root("bad_pack_id")
    try:
        pack_root = case_root / "bad_pack_id"
        _write_minimal_pack(pack_root)
        manifest_path = pack_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pack_id"] = "other_pack"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "pack_id 与目录名不一致" in ";".join(exc_info.value.diagnostics)
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_bad_exit_reference() -> None:
    """
    功能：验证 scene exits 不能指向不存在场景。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示场景引用完整性校验失效。
    """
    case_root = _make_case_root("bad_exit")
    try:
        pack_root = case_root / "bad_exit"
        _write_minimal_pack(pack_root, target_scene_id="missing")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "出口指向不存在场景" in ";".join(exc_info.value.diagnostics)
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_duplicate_interaction_ids() -> None:
    """
    功能：验证同一 scene 内 interaction_id 必须唯一。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示交互入口唯一性校验失效。
    """
    case_root = _make_case_root("bad_interaction")
    try:
        pack_root = case_root / "bad_interaction"
        _write_minimal_pack(pack_root)
        scene_path = pack_root / "scenes" / "start.json"
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        scene["interactables"].append(
            {
                "interaction_id": "inspect_start",
                "label": "重复观察起点",
                "kind": "inspect",
            }
        )
        scene_path.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "interaction_id 重复" in ";".join(exc_info.value.diagnostics)
    finally:
        _remove_case_root(case_root)


def test_validate_story_pack_rejects_lore_path_escape() -> None:
    """
    功能：验证 manifest.lore_files 不能通过相对路径越出 pack/lore 目录。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：未抛 StoryPackValidationError 表示 lore 边界校验失效。
    """
    case_root = _make_case_root("bad_lore_escape")
    try:
        pack_root = case_root / "bad_lore_escape"
        _write_minimal_pack(pack_root)
        (case_root / "outside.md").write_text("越界 lore", encoding="utf-8")
        manifest_path = pack_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["lore_files"] = ["../outside.md"]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(StoryPackValidationError) as exc_info:
            validate_story_pack(pack_root)

        assert "lore 文件越界" in ";".join(exc_info.value.diagnostics)
    finally:
        _remove_case_root(case_root)


def test_story_pack_registry_excludes_invalid_pack() -> None:
    """
    功能：验证 registry 只暴露合法 pack，并保留坏包诊断。
    入参：无，使用 test_runs 下自管临时目录。
    出参：None。
    异常：断言失败表示坏包污染可选列表。
    """
    case_root = _make_case_root("registry")
    try:
        good = case_root / "good_pack"
        bad = case_root / "bad_pack"
        _write_minimal_pack(good)
        bad.mkdir()

        registry = StoryPackRegistry(case_root)
        registry.refresh()

        summaries = registry.list_summaries()
        assert [item["pack_id"] for item in summaries] == ["good_pack"]
        assert "bad_pack" in registry.diagnostics()
    finally:
        _remove_case_root(case_root)
