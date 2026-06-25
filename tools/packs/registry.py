"""
A2 Story Pack 本地 registry 与校验器。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from state.contracts.quest import QuestDef
from state.contracts.story_pack import (
    StoryPackBundle,
    StoryPackManifest,
    StoryPackSceneDef,
    StoryPackSummary,
    StoryPackVisibleRefDef,
)
from state.contracts.trigger import TriggerDef
from tools.packs.path_safety import normalize_relative_content_name, validate_file_identifier

STORY_PACK_ASSET_MIME_BY_EXTENSION: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}
STORY_PACK_ASSET_MEDIA_TYPE_BY_EXTENSION: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "gif",
    ".mp4": "video",
    ".webm": "video",
    ".ogv": "video",
    ".mov": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".flac": "audio",
}
ALLOWED_ASSET_EXTENSIONS = frozenset(STORY_PACK_ASSET_MIME_BY_EXTENSION)


@dataclass
class _LoadedStoryPackScenes:
    """
    功能：承载已加载场景模型与原始 payload，避免 validate_story_pack 混合解析和校验细节。
    入参：scenes（dict[str, StoryPackSceneDef]）：按 scene_id 索引的场景模型；
        payloads（dict[str, Any]）：同一 scene_id 对应的原始 JSON 对象。
    出参：_LoadedStoryPackScenes。
    异常：数据类本身不抛异常；字段一致性由 _load_pack_scenes 保证。
    """

    scenes: dict[str, StoryPackSceneDef]
    payloads: dict[str, Any]


def story_pack_asset_mime_type_for_src(src: str) -> str:
    """
    功能：根据 Story Pack asset src 推断稳定 MIME 类型。
    入参：src（str）：manifest.assets.<id>.src 或 assets/ 下相对路径。
    出参：str，白名单命中时返回固定 MIME，否则返回 application/octet-stream。
    异常：不抛异常；未知扩展名由调用方在白名单校验中处理。
    """
    return STORY_PACK_ASSET_MIME_BY_EXTENSION.get(
        Path(src).suffix.lower(),
        "application/octet-stream",
    )


def story_pack_asset_media_type_for_src(src: str) -> str:
    """
    功能：根据 Story Pack asset src 推断 image/gif/video/audio 媒体类型。
    入参：src（str）：manifest.assets.<id>.src 或 assets/ 下相对路径。
    出参：str，白名单命中时返回媒体类型，否则返回 image 作为兼容兜底。
    异常：不抛异常；未知扩展名由调用方在白名单校验中处理。
    """
    return STORY_PACK_ASSET_MEDIA_TYPE_BY_EXTENSION.get(Path(src).suffix.lower(), "image")


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


def _compute_pack_hash(
    manifest_payload: dict[str, Any],
    scenes_payload: dict[str, Any],
    quests_payload: dict[str, Any] | None = None,
    triggers_payload: dict[str, Any] | None = None,
    assets_payload: dict[str, Any] | None = None,
) -> str:
    """
    功能：根据 manifest、scenes 与可选资源摘要的规范化 JSON 内容生成编译摘要 hash。
    入参：manifest_payload（dict）：manifest 原始对象；
        scenes_payload（dict）：按 scene_id 索引的场景对象；
        quests_payload/triggers_payload/assets_payload（dict | None）：任务、触发器和
        多媒体资源摘要。
    出参：str，sha256 前 16 位，足够用于 A2-Core 会话绑定诊断。
    异常：JSON 序列化异常向上抛出，表示 pack 含不可序列化值。
    """
    canonical = json.dumps(
        {
            "manifest": manifest_payload,
            "scenes": scenes_payload,
            "quests": quests_payload or {},
            "triggers": triggers_payload or {},
            "assets": assets_payload or {},
        },
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


def _iter_scene_asset_refs(scene: StoryPackSceneDef) -> list[str]:
    """
    功能：收集单个场景直接或间接引用的多媒体物料 ID。
    入参：scene（StoryPackSceneDef）：已通过 Pydantic 校验的场景定义。
    出参：list[str]，可能为空，调用方负责校验是否存在。
    异常：不抛异常；空引用会被过滤。
    """
    refs: list[str] = []
    for asset_id in (scene.background_asset_id, scene.image_asset_id):
        if asset_id:
            refs.append(asset_id)
    for exit_def in scene.exits:
        if exit_def.asset_id:
            refs.append(exit_def.asset_id)
    for interaction in scene.interactables:
        if interaction.asset_id:
            refs.append(interaction.asset_id)
    for visible in [*scene.visible_npcs, *scene.visible_items]:
        if isinstance(visible, StoryPackVisibleRefDef):
            for asset_id in (
                visible.asset_id,
                visible.portrait_asset_id,
                visible.icon_asset_id,
                visible.image_asset_id,
            ):
                if asset_id:
                    refs.append(asset_id)
    return refs


def _validate_pack_assets(
    root: Path,
    manifest: StoryPackManifest,
    scenes: dict[str, StoryPackSceneDef],
    diagnostics: list[str],
) -> dict[str, Any]:
    """
    功能：校验 manifest.assets 与场景 asset 引用，并返回用于 compiled hash 的媒体摘要。
    入参：root（Path）：pack 根目录；manifest（StoryPackManifest）：入口定义；
        scenes（dict[str, StoryPackSceneDef]）：已加载场景；diagnostics（list[str]）：可追加诊断。
    出参：dict[str, Any]，键为 asset_id，值包含 src 和文件 digest。
    异常：不抛业务异常；路径或读文件失败会追加 diagnostics。
    """
    assets_dir = (root / "assets").resolve()
    asset_hash_payloads: dict[str, Any] = {}
    for asset_id, asset in manifest.assets.items():
        try:
            validate_file_identifier(asset_id, "asset_id")
        except ValueError as error:
            diagnostics.append(str(error))
            continue
        try:
            relative_src = normalize_relative_content_name(asset.src, "assets")
        except ValueError as error:
            diagnostics.append(str(error))
            continue
        suffix = Path(relative_src).suffix.lower()
        if suffix not in ALLOWED_ASSET_EXTENSIONS:
            diagnostics.append(f"asset {asset_id} 扩展名非法: {suffix or '<none>'}")
            continue
        inferred_mime_type = story_pack_asset_mime_type_for_src(relative_src)
        declared_mime_type = str(asset.mime_type or "").strip().lower()
        if declared_mime_type and declared_mime_type != inferred_mime_type:
            diagnostics.append(
                f"asset {asset_id} MIME 不匹配: {declared_mime_type} != {inferred_mime_type}"
            )
            continue
        inferred_media_type = story_pack_asset_media_type_for_src(relative_src)
        if asset.media_type and asset.media_type != inferred_media_type:
            diagnostics.append(
                f"asset {asset_id} media_type 不匹配: {asset.media_type} != {inferred_media_type}"
            )
            continue
        asset_path = (assets_dir / relative_src).resolve()
        if not _is_relative_to(asset_path, assets_dir):
            diagnostics.append(f"asset 文件越界: assets/{relative_src}")
            continue
        if not asset_path.exists() or not asset_path.is_file():
            diagnostics.append(f"asset 文件不存在: assets/{relative_src}")
            continue
        try:
            digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()[:16]
        except OSError as error:
            diagnostics.append(f"asset {asset_id} 读取失败: {error}")
            continue
        asset_hash_payloads[asset_id] = {
            "src": relative_src,
            "digest": digest,
            "kind": asset.kind,
            "media_type": inferred_media_type,
            "mime_type": declared_mime_type or inferred_mime_type,
        }
        if asset.playback is not None:
            playback_payload = asset.playback.model_dump(
                mode="json",
                exclude_defaults=True,
                exclude_none=True,
            )
            if playback_payload:
                asset_hash_payloads[asset_id]["playback"] = playback_payload

    declared_asset_ids = set(manifest.assets)
    for scene in scenes.values():
        for asset_id in _iter_scene_asset_refs(scene):
            if asset_id not in declared_asset_ids:
                diagnostics.append(f"scene {scene.scene_id} 引用未声明 asset: {asset_id}")
    return asset_hash_payloads


def _load_pack_manifest(root: Path) -> tuple[dict[str, Any], StoryPackManifest]:
    """
    功能：读取并校验 Story Pack manifest.json。
    入参：root（Path）：pack 根目录，必须包含 manifest.json。
    出参：tuple[dict[str, Any], StoryPackManifest]，原始 payload 与 Pydantic 模型。
    异常：缺文件、读取失败或 schema 错误时抛 StoryPackValidationError。
    """
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
    return manifest_payload, manifest


def _validate_manifest_identity(
    root: Path,
    manifest: StoryPackManifest,
    diagnostics: list[str],
) -> None:
    """
    功能：校验 manifest.pack_id 与目录名一致，避免 registry key 与内容身份分裂。
    入参：root（Path）：pack 根目录；manifest（StoryPackManifest）：入口定义；
        diagnostics（list[str]）：可追加诊断。
    出参：None。
    异常：不抛异常；不一致时追加 diagnostics，交由总入口统一抛出。
    """
    if manifest.pack_id != root.name:
        diagnostics.append(f"pack_id 与目录名不一致: {manifest.pack_id} != {root.name}")


def _load_pack_triggers(
    root: Path,
    diagnostics: list[str],
) -> tuple[set[str], dict[str, TriggerDef]]:
    """
    功能：加载 triggers/*.json 并校验触发器 ID 唯一性。
    入参：root（Path）：pack 根目录；diagnostics（list[str]）：可追加诊断。
    出参：tuple[set[str], dict[str, TriggerDef]]，触发器 ID 集合与模型索引。
    异常：不抛业务异常；单个触发器读取或 schema 错误会追加 diagnostics 并继续。
    """
    triggers_dir = root / "triggers"
    trigger_ids: set[str] = set()
    triggers: dict[str, TriggerDef] = {}
    if not triggers_dir.exists() or not triggers_dir.is_dir():
        return trigger_ids, triggers

    for trigger_path in sorted(triggers_dir.glob("*.json")):
        try:
            trigger_payload = _read_json_object(trigger_path)
            trigger_def = TriggerDef.model_validate(trigger_payload)
        except (OSError, ValueError) as error:
            diagnostics.append(f"triggers/{trigger_path.name} 读取失败: {error}")
            continue
        except ValidationError as error:
            diagnostics.extend(_validation_to_messages(f"triggers/{trigger_path.name}", error))
            continue
        trigger_id = trigger_def.trigger_id
        if trigger_id in trigger_ids:
            diagnostics.append(f"触发器 ID 重复: {trigger_id}")
            continue
        trigger_ids.add(trigger_id)
        triggers[trigger_id] = trigger_def
    return trigger_ids, triggers


def _load_pack_scenes(root: Path, diagnostics: list[str]) -> _LoadedStoryPackScenes:
    """
    功能：加载 scenes/*.json，校验 scene schema 与 scene_id 唯一性。
    入参：root（Path）：pack 根目录；diagnostics（list[str]）：可追加诊断。
    出参：_LoadedStoryPackScenes，包含有效场景模型与原始 payload。
    异常：缺少 scenes/ 目录时抛 StoryPackValidationError；单文件错误追加 diagnostics。
    """
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
    return _LoadedStoryPackScenes(scenes=scenes, payloads=scene_payloads)


def _validate_scene_references(
    *,
    manifest: StoryPackManifest,
    scenes: dict[str, StoryPackSceneDef],
    scene_payloads: dict[str, Any],
    trigger_ids: set[str],
    diagnostics: list[str],
) -> None:
    """
    功能：校验场景起点、出口、交互 ID、交互 kind 与触发器引用完整性。
    入参：manifest（StoryPackManifest）：入口定义；scenes（dict）：场景索引；
        scene_payloads（dict[str, Any]）：原始场景 JSON；trigger_ids（set[str]）：已加载触发器；
        diagnostics（list[str]）：可追加诊断。
    出参：None。
    异常：不抛异常；所有引用错误追加 diagnostics。
    """
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

    _allowed_interactable_kinds = frozenset(
        {"observe", "talk", "inspect", "use_item", "attack", "custom"}
    )
    for scene_id, raw in scene_payloads.items():
        raw_interactables = raw.get("interactables")
        if raw_interactables and isinstance(raw_interactables, list):
            for interactable in raw_interactables:
                if (
                    isinstance(interactable, dict)
                    and interactable.get("kind") not in _allowed_interactable_kinds
                ):
                    interaction_id = interactable.get("interaction_id", "?")
                    diagnostics.append(
                        f"场景 {scene_id} interactable {interaction_id} "
                        f"kind 非法: {interactable.get('kind')}"
                    )

    for scene_id, raw in scene_payloads.items():
        raw_triggers = raw.get("triggers")
        if raw_triggers and isinstance(raw_triggers, list):
            for trigger_ref in raw_triggers:
                if isinstance(trigger_ref, str) and trigger_ref not in trigger_ids:
                    diagnostics.append(f"场景 {scene_id} 引用不存在的触发器: {trigger_ref}")


def _validate_lore_files(
    root: Path,
    manifest: StoryPackManifest,
    diagnostics: list[str],
) -> None:
    """
    功能：校验 manifest.lore_files 是否存在且未越出 pack/lore 边界。
    入参：root（Path）：pack 根目录；manifest（StoryPackManifest）：入口定义；
        diagnostics（list[str]）：可追加诊断。
    出参：None。
    异常：不抛异常；越界或缺文件均追加 diagnostics。
    """
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


def _load_pack_quests(
    root: Path,
    trigger_ids: set[str],
    diagnostics: list[str],
) -> dict[str, QuestDef]:
    """
    功能：加载 quests/*.json，并校验任务 ID 唯一性和 stage 触发器引用。
    入参：root（Path）：pack 根目录；trigger_ids（set[str]）：已加载触发器 ID；
        diagnostics（list[str]）：可追加诊断。
    出参：dict[str, QuestDef]，按 quest_id 索引的任务模型。
    异常：不抛业务异常；单个任务读取或 schema 错误追加 diagnostics 并继续。
    """
    quests_dir = root / "quests"
    quests: dict[str, QuestDef] = {}
    if not quests_dir.exists() or not quests_dir.is_dir():
        return quests

    for quest_path in sorted(quests_dir.glob("*.json")):
        try:
            quest_payload = _read_json_object(quest_path)
            quest_def = QuestDef.model_validate(quest_payload)
        except (OSError, ValueError) as error:
            diagnostics.append(f"quests/{quest_path.name} 读取失败: {error}")
            continue
        except ValidationError as error:
            diagnostics.extend(_validation_to_messages(f"quests/{quest_path.name}", error))
            continue
        quest_id = quest_def.quest_id
        if quest_id in quests:
            diagnostics.append(f"任务 ID 重复: {quest_id}")
            continue
        for stage in quest_def.stages:
            for trigger_ref in stage.triggers_on_activate:
                if trigger_ref not in trigger_ids:
                    diagnostics.append(
                        f"任务 {quest_id} stage {stage.stage_id} triggers_on_activate "
                        f"引用不存在的触发器: {trigger_ref}"
                    )
            for trigger_ref in stage.triggers_on_complete:
                if trigger_ref not in trigger_ids:
                    diagnostics.append(
                        f"任务 {quest_id} stage {stage.stage_id} triggers_on_complete "
                        f"引用不存在的触发器: {trigger_ref}"
                    )
        quests[quest_id] = quest_def
    return quests


def _build_story_pack_summary(
    *,
    manifest: StoryPackManifest,
    scenes: dict[str, StoryPackSceneDef],
    quests: dict[str, QuestDef],
    triggers: dict[str, TriggerDef],
    manifest_payload: dict[str, Any],
    scene_payloads: dict[str, Any],
    asset_hash_payloads: dict[str, Any],
) -> StoryPackSummary:
    """
    功能：根据已校验内容构建 StoryPackSummary 与 compiled_artifact_hash。
    入参：manifest/scenes/quests/triggers 为已加载模型；
        manifest_payload/scene_payloads 为原始 JSON；
        asset_hash_payloads（dict[str, Any]）：媒体文件摘要。
    出参：StoryPackSummary，可写入 registry 对外摘要。
    异常：JSON 序列化或 hash 构建异常向上抛出，表示 pack 内容不可规范化。
    """
    quest_hash_payloads = {
        quest_id: quest.model_dump(mode="json")
        for quest_id, quest in sorted(quests.items(), key=lambda item: item[0])
    }
    trigger_hash_payloads = {
        trigger_id: trigger.model_dump(mode="json")
        for trigger_id, trigger in sorted(triggers.items(), key=lambda item: item[0])
    }
    pack_hash = _compute_pack_hash(
        manifest_payload,
        scene_payloads,
        quest_hash_payloads,
        trigger_hash_payloads,
        asset_hash_payloads,
    )
    interaction_count = sum(len(scene.interactables) for scene in scenes.values())
    start_scene = scenes[manifest.start_scene_id]
    return StoryPackSummary(
        pack_id=manifest.pack_id,
        title=manifest.title,
        version=manifest.version,
        scenario_id=manifest.scenario_id,
        start_scene_id=manifest.start_scene_id,
        start_scene_title=start_scene.display_name,
        compiled_artifact_hash=pack_hash,
        source_background_hash=manifest.source_background_hash,
        scene_count=len(scenes),
        interaction_count=interaction_count,
        quest_count=len(quests),
        trigger_count=len(triggers),
        asset_count=len(manifest.assets),
        diagnostics=[],
    )


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

    manifest_payload, manifest = _load_pack_manifest(root)
    _validate_manifest_identity(root, manifest, diagnostics)
    trigger_ids, triggers = _load_pack_triggers(root, diagnostics)
    loaded_scenes = _load_pack_scenes(root, diagnostics)
    scenes = loaded_scenes.scenes
    scene_payloads = loaded_scenes.payloads
    _validate_scene_references(
        manifest=manifest,
        scenes=scenes,
        scene_payloads=scene_payloads,
        trigger_ids=trigger_ids,
        diagnostics=diagnostics,
    )
    _validate_lore_files(root, manifest, diagnostics)

    asset_hash_payloads = _validate_pack_assets(root, manifest, scenes, diagnostics)
    quests = _load_pack_quests(root, trigger_ids, diagnostics)

    if diagnostics:
        raise StoryPackValidationError(diagnostics)

    summary = _build_story_pack_summary(
        manifest=manifest,
        scenes=scenes,
        quests=quests,
        triggers=triggers,
        manifest_payload=manifest_payload,
        scene_payloads=scene_payloads,
        asset_hash_payloads=asset_hash_payloads,
    )
    return StoryPackBundle(
        manifest=manifest,
        scenes=scenes,
        quests=quests,
        triggers=triggers,
        summary=summary,
    )


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
        self._signature: tuple[tuple[str, str, int, int], ...] | None = None

    def _build_signature(self) -> tuple[tuple[str, str, int, int], ...]:
        """
        功能：为 registry 根目录生成轻量文件签名，用于跳过未变化目录的重复校验。
        入参：无，读取 self.root 下目录名、文件大小和 mtime_ns。
        出参：tuple[tuple[str, str, int, int], ...]，元素为 kind/path/mtime_ns/size。
        异常：单个路径 stat 失败时忽略该路径，下一次 refresh 会重新比较签名。
        """
        if not self.root.exists():
            return (("missing", ".", 0, 0),)
        entries: list[tuple[str, str, int, int]] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            try:
                child_stat = child.stat()
            except OSError:
                continue
            child_name = child.name.replace("\\", "/")
            entries.append(("dir", child_name, int(child_stat.st_mtime_ns), 0))
            for path in sorted(child.rglob("*")):
                try:
                    stat_result = path.stat()
                    relative = str(path.relative_to(self.root)).replace("\\", "/")
                except OSError:
                    continue
                kind = "dir" if path.is_dir() else "file"
                size = int(stat_result.st_size) if path.is_file() else 0
                entries.append((kind, relative, int(stat_result.st_mtime_ns), size))
        return tuple(entries)

    def refresh(self, *, force: bool = False) -> None:
        """
        功能：重新扫描 story_packs 根目录，仅把合法 pack 放入可选 registry。
        入参：force（bool，默认 False）：为 True 时忽略目录签名缓存，强制重新校验。
        出参：None。
        异常：不抛业务异常；坏 pack 进入 diagnostics，避免污染运行时可选列表。
        """
        signature = self._build_signature()
        if not force and self._signature == signature:
            return
        packs: dict[str, StoryPackBundle] = {}
        diagnostics: dict[str, list[str]] = {}
        if not self.root.exists():
            self._packs = packs
            self._diagnostics = {"story_packs": [f"目录不存在: {self.root}"]}
            self._signature = signature
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
        self._signature = signature

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
