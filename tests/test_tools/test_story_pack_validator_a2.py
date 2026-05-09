from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from tools.packs.registry import (
    StoryPackRegistry,
    StoryPackValidationError,
    validate_story_pack,
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
    功能：验证官方 A2-Core demo pack 可被校验器接受。
    入参：无。
    出参：None。
    异常：断言失败表示 demo pack 或 v0 契约漂移。
    """
    bundle = validate_story_pack("story_packs/demo_a2_core")

    assert bundle.summary.pack_id == "demo_a2_core"
    assert bundle.summary.scene_count >= 3
    assert bundle.summary.interaction_count >= 2
    assert bundle.summary.compiled_artifact_hash


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
