"""
A2 Story Pack 本地 registry 与校验器。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from state.contracts.story_pack import (
    StoryPackBundle,
    StoryPackManifest,
    StoryPackSceneDef,
    StoryPackSummary,
)


class StoryPackValidationError(ValueError):
    """
    功能：表示 Story Pack 校验失败并携带可展示诊断。
    入参：diagnostics（list[str]）：校验错误列表。
    出参：StoryPackValidationError。
    异常：初始化不抛额外异常。
    """

    def __init__(self, diagnostics: list[str]) -> None:
        """
        功能：保存诊断信息并构造异常文本。
        入参：diagnostics（list[str]）：校验失败原因。
        出参：None。
        异常：无。
        """
        self.diagnostics = diagnostics
        super().__init__("; ".join(diagnostics))


def _read_json_object(path: Path) -> dict[str, Any]:
    """
    功能：读取 JSON 文件并要求顶层为对象。
    入参：path（Path）：JSON 文件路径。
    出参：dict[str, Any]，顶层对象。
    异常：文件不存在、JSON 解析失败或顶层非对象时抛出 ValueError/FileNotFoundError。
    """
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} 顶层必须是 JSON 对象")
    return loaded


def _validation_to_messages(prefix: str, error: ValidationError) -> list[str]:
    """
    功能：把 Pydantic ValidationError 转成稳定中文诊断。
    入参：prefix（str）：错误来源；error（ValidationError）：原始校验异常。
    出参：list[str]，可返回 API/CLI 的诊断文本。
    异常：不抛异常；无法读取字段位置时降级为错误类型。
    """
    messages: list[str] = []
    for item in error.errors():
        loc = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg") or item.get("type") or "字段非法")
        messages.append(f"{prefix}.{loc}: {message}" if loc else f"{prefix}: {message}")
    return messages


def _compute_pack_hash(manifest_payload: dict[str, Any], scenes_payload: dict[str, Any]) -> str:
    """
    功能：根据 manifest 与 scenes 的规范化 JSON 内容生成编译摘要 hash。
    入参：manifest_payload（dict）：manifest 原始对象；
        scenes_payload（dict）：按 scene_id 索引的场景对象。
    出参：str，sha256 前 16 位，足够用于 A2-Core 会话绑定诊断。
    异常：JSON 序列化异常向上抛出，表示 pack 含不可序列化值。
    """
    canonical = json.dumps(
        {"manifest": manifest_payload, "scenes": scenes_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _is_relative_to(child: Path, parent: Path) -> bool:
    """
    功能：判断路径 child 解析后是否仍位于 parent 目录内。
    入参：child（Path）：待检查路径；parent（Path）：允许的根目录。
    出参：bool，child 在 parent 内返回 True。
    异常：不抛异常；路径不存在时由调用方在存在性检查中处理。
    """
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_story_pack(pack_path: str | Path) -> StoryPackBundle:
    """
    功能：校验本地 Story Pack 文件夹并返回已编译摘要。
    入参：pack_path（str | Path）：pack 根目录，必须包含 manifest.json 与 scenes/*.json。
    出参：StoryPackBundle，包含 manifest、scene 索引和 registry 摘要。
    异常：StoryPackValidationError，诊断包含缺文件、schema 错误和引用错误。
    """
    root = Path(pack_path)
    diagnostics: list[str] = []
    if not root.exists() or not root.is_dir():
        raise StoryPackValidationError([f"pack 目录不存在: {root}"])

    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise StoryPackValidationError([f"缺少 manifest.json: {manifest_path}"])

    try:
        manifest_payload = _read_json_object(manifest_path)
        manifest = StoryPackManifest.model_validate(manifest_payload)
    except (OSError, ValueError) as error:
        raise StoryPackValidationError([f"manifest.json 读取失败: {error}"]) from error
    except ValidationError as error:
        raise StoryPackValidationError(_validation_to_messages("manifest", error)) from error

    # 准入边界：pack_id 必须与目录名一致，避免 registry key 与内容身份分裂。
    if manifest.pack_id != root.name:
        diagnostics.append(f"pack_id 与目录名不一致: {manifest.pack_id} != {root.name}")

    scenes_dir = root / "scenes"
    if not scenes_dir.exists() or not scenes_dir.is_dir():
        raise StoryPackValidationError(["缺少 scenes/ 目录"])

    scenes: dict[str, StoryPackSceneDef] = {}
    scene_payloads: dict[str, Any] = {}
    for scene_path in sorted(scenes_dir.glob("*.json")):
        try:
            payload = _read_json_object(scene_path)
            scene = StoryPackSceneDef.model_validate(payload)
        except (OSError, ValueError) as error:
            diagnostics.append(f"{scene_path.name} 读取失败: {error}")
            continue
        except ValidationError as error:
            diagnostics.extend(_validation_to_messages(scene_path.name, error))
            continue
        if scene.scene_id in scenes:
            diagnostics.append(f"重复 scene_id: {scene.scene_id}")
            continue
        scenes[scene.scene_id] = scene
        scene_payloads[scene.scene_id] = payload

    if not scenes:
        diagnostics.append("scenes/ 至少需要 1 个有效场景")
    if manifest.start_scene_id not in scenes:
        diagnostics.append(f"start_scene_id 不存在: {manifest.start_scene_id}")

    for scene in scenes.values():
        interaction_ids: set[str] = set()
        for interaction in scene.interactables:
            if interaction.interaction_id in interaction_ids:
                diagnostics.append(
                    f"scene {scene.scene_id} interaction_id 重复: {interaction.interaction_id}"
                )
            interaction_ids.add(interaction.interaction_id)
        for exit_def in scene.exits:
            if exit_def.target_scene_id not in scenes:
                diagnostics.append(
                    f"scene {scene.scene_id} 出口指向不存在场景: {exit_def.target_scene_id}"
                )

    root_resolved = root.resolve()
    lore_dir = (root / "lore").resolve()
    for lore_file in manifest.lore_files:
        lore_path = (root / "lore" / lore_file).resolve()
        # A2-Core 只验证 lore 文件存在和边界，运行时不把 lore 作为确定性状态源。
        if not _is_relative_to(lore_path, root_resolved) or not _is_relative_to(
            lore_path,
            lore_dir,
        ):
            diagnostics.append(f"lore 文件越界: lore/{lore_file}")
            continue
        if not lore_path.exists():
            diagnostics.append(f"lore 文件不存在: lore/{lore_file}")

    if diagnostics:
        raise StoryPackValidationError(diagnostics)

    pack_hash = _compute_pack_hash(manifest_payload, scene_payloads)
    interaction_count = sum(len(scene.interactables) for scene in scenes.values())
    summary = StoryPackSummary(
        pack_id=manifest.pack_id,
        title=manifest.title,
        version=manifest.version,
        scenario_id=manifest.scenario_id,
        start_scene_id=manifest.start_scene_id,
        compiled_artifact_hash=pack_hash,
        scene_count=len(scenes),
        interaction_count=interaction_count,
        diagnostics=[],
    )
    return StoryPackBundle(manifest=manifest, scenes=scenes, summary=summary)


class StoryPackRegistry:
    """
    功能：只读扫描本地 story_packs 目录，提供有效 pack 查询能力。
    入参：root（str | Path）：story_packs 根目录。
    出参：StoryPackRegistry。
    异常：初始化不读取文件；扫描阶段把坏 pack 记录为诊断而非抛出。
    """

    def __init__(self, root: str | Path) -> None:
        """
        功能：保存 registry 根目录。
        入参：root（str | Path）：本地 story_packs 根路径。
        出参：None。
        异常：无。
        """
        self.root = Path(root)
        self._packs: dict[str, StoryPackBundle] = {}
        self._diagnostics: dict[str, list[str]] = {}

    def refresh(self) -> None:
        """
        功能：重新扫描 story_packs 根目录，仅把合法 pack 放入可选 registry。
        入参：无。
        出参：None。
        异常：不抛业务异常；坏 pack 进入 diagnostics，避免污染运行时可选列表。
        """
        packs: dict[str, StoryPackBundle] = {}
        diagnostics: dict[str, list[str]] = {}
        if not self.root.exists():
            self._packs = packs
            self._diagnostics = {"story_packs": [f"目录不存在: {self.root}"]}
            return
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            try:
                bundle = validate_story_pack(child)
            except StoryPackValidationError as error:
                diagnostics[child.name] = error.diagnostics
                continue
            if bundle.summary.pack_id in packs:
                diagnostics[child.name] = [f"pack_id 重复: {bundle.summary.pack_id}"]
                continue
            packs[bundle.summary.pack_id] = bundle
        self._packs = packs
        self._diagnostics = diagnostics

    def list_summaries(self) -> list[dict[str, Any]]:
        """
        功能：返回所有合法 pack 摘要，按 pack_id 排序。
        入参：无。
        出参：list[dict[str, Any]]，可直接 JSON 序列化。
        异常：不抛异常；字段序列化由 Pydantic 保证。
        """
        return [
            bundle.summary.model_dump()
            for pack_id, bundle in sorted(self._packs.items(), key=lambda item: item[0])
        ]

    def get(self, pack_id: str) -> StoryPackBundle | None:
        """
        功能：按 pack_id 查询已校验 pack。
        入参：pack_id（str）：Story Pack 稳定 ID。
        出参：StoryPackBundle | None，未找到返回 None。
        异常：不抛异常。
        """
        return self._packs.get(pack_id)

    def diagnostics(self) -> dict[str, list[str]]:
        """
        功能：返回最近一次 refresh 的坏包诊断。
        入参：无。
        出参：dict[str, list[str]]，key 为目录名。
        异常：不抛异常。
        """
        return {key: list(value) for key, value in self._diagnostics.items()}
