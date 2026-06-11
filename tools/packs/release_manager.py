"""
A2-Release Story Pack 导入与删除管理服务。
"""

from __future__ import annotations

import base64
import binascii
import json
import shutil
from pathlib import Path
from typing import Any

from tools.packs.path_safety import (
    normalize_relative_content_name,
    resolve_json_file_path,
    resolve_pack_directory,
    resolve_relative_content_path,
    validate_file_identifier,
    validate_pack_identifier,
)
from tools.packs.registry import STORY_PACK_ASSET_MIME_BY_EXTENSION, StoryPackRegistry

MAX_ASSET_FILE_BYTES = 5 * 1024 * 1024


class StoryPackReleaseError(ValueError):
    """
    功能：表示 Story Pack 发布管理操作失败，并携带 API 可返回的错误码。
    入参：code（str）：业务错误码；message（str）：用户可见错误；status_code（int）：HTTP 状态码。
    出参：StoryPackReleaseError。
    异常：初始化不抛额外异常。
    """

    def __init__(self, code: str, message: str, status_code: int) -> None:
        """
        功能：保存发布管理错误上下文。
        入参：code（str）：错误码；message（str）：错误说明；status_code（int）：HTTP 状态码。
        出参：None。
        异常：无。
        """
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def import_story_pack_payload(
    registry: StoryPackRegistry,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：把前端上传的 Story Pack JSON 文件集合写入 registry 根目录并复验。
    入参：registry（StoryPackRegistry）：目标本地注册表；payload（dict[str, Any]）：
        包含 manifest、scenes，可选 lore、quests、triggers、asset_files。
    出参：dict[str, Any]，包含导入后已校验 summary。
    异常：StoryPackReleaseError；字段缺失、目录冲突或复验失败时抛出，并清理本次写入。
    """
    manifest = _require_object(payload.get("manifest"), "manifest")
    try:
        pack_id = validate_pack_identifier(str(manifest.get("pack_id") or ""))
    except ValueError as exc:
        raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
    scenes = _normalize_file_collection(payload.get("scenes"), "scenes", "scene_id")
    lore = _normalize_text_file_collection(payload.get("lore"), "lore")
    quests = _normalize_file_collection(payload.get("quests"), "quests", "quest_id", required=False)
    triggers = _normalize_file_collection(
        payload.get("triggers"),
        "triggers",
        "trigger_id",
        required=False,
    )
    asset_files = _normalize_asset_file_collection(payload.get("asset_files"))
    _validate_asset_files_declared(manifest, asset_files)

    root = Path(str(registry.root))
    root.mkdir(parents=True, exist_ok=True)
    target = _safe_pack_dir(root, pack_id)
    if target.exists():
        raise StoryPackReleaseError("PACK_ALREADY_EXISTS", f"pack 已存在: {pack_id}", 409)

    try:
        _write_pack_directory(target, manifest, scenes, lore, quests, triggers, asset_files)
    except ValueError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise StoryPackReleaseError("SERVER_ERROR", f"写入剧本包失败: {exc}", 500) from exc

    registry.refresh()
    bundle = registry.get(pack_id)
    if bundle is None:
        diagnostics = registry.diagnostics().get(pack_id, ["导入后未通过校验"])
        shutil.rmtree(target, ignore_errors=True)
        registry.refresh()
        raise StoryPackReleaseError("PACK_IMPORT_FAILED", "；".join(diagnostics), 400)

    return {"summary": bundle.summary.model_dump()}


def delete_story_pack(registry: StoryPackRegistry, pack_id: str) -> dict[str, Any]:
    """
    功能：删除本地 Story Pack 目录，并刷新 registry。
    入参：registry（StoryPackRegistry）：目标注册表；pack_id（str）：要删除的 pack 目录名。
    出参：dict[str, Any]，包含 deleted_pack_id。
    异常：StoryPackReleaseError；缺失目录或路径越界时抛出。
    """
    try:
        normalized = validate_pack_identifier(pack_id)
    except ValueError as exc:
        raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
    root = Path(str(registry.root))
    target = _safe_pack_dir(root, normalized)
    if not target.exists() or not target.is_dir():
        raise StoryPackReleaseError("PACK_NOT_FOUND", "pack_id 不存在", 404)

    shutil.rmtree(target)
    registry.refresh()
    return {"deleted_pack_id": normalized}


def _require_object(value: Any, field_name: str) -> dict[str, Any]:
    """
    功能：校验请求字段为 JSON 对象。
    入参：value（Any）：待检查值；field_name（str）：字段名。
    出参：dict[str, Any]，原对象。
    异常：StoryPackReleaseError；字段不是对象时抛出 INVALID_ARGUMENT。
    """
    if not isinstance(value, dict):
        raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} 必须是 JSON 对象", 400)
    return value


def _normalize_file_collection(
    value: Any,
    field_name: str,
    id_field: str,
    required: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    功能：把 dict 或 list 形式的 JSON 文件集合规整为按稳定 ID 索引的对象字典。
    入参：value（Any）：请求字段；field_name（str）：字段名；id_field（str）：对象内 ID 字段；
        required（bool，默认 True）：是否要求至少一项。
    出参：dict[str, dict[str, Any]]。
    异常：StoryPackReleaseError；集合形状、对象类型或 ID 缺失非法时抛出。
    """
    if value is None:
        if required:
            raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} 不能为空", 400)
        return {}
    if isinstance(value, dict):
        raw_items = list(value.values())
    elif isinstance(value, list):
        raw_items = value
    else:
        raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} 必须是对象或数组", 400)

    normalized: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        item = _require_object(raw_item, field_name)
        raw_item_id = str(item.get(id_field) or "")
        if not raw_item_id.strip():
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"{field_name} 条目缺少 {id_field}",
                400,
            )
        try:
            item_id = validate_file_identifier(raw_item_id, id_field)
        except ValueError as exc:
            raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
        if item_id in normalized:
            raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} ID 重复: {item_id}", 400)
        normalized[item_id] = item
    if required and not normalized:
        raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} 至少需要 1 项", 400)
    return normalized


def _normalize_text_file_collection(value: Any, field_name: str) -> dict[str, str]:
    """
    功能：校验 lore 文本文件集合，限制为相对文件名到文本内容的映射。
    入参：value（Any）：请求字段；field_name（str）：字段名。
    出参：dict[str, str]。
    异常：StoryPackReleaseError；字段不是对象、文件名越界或内容非字符串时抛出。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StoryPackReleaseError("INVALID_ARGUMENT", f"{field_name} 必须是 JSON 对象", 400)
    normalized: dict[str, str] = {}
    for raw_name, raw_content in value.items():
        try:
            filename = normalize_relative_content_name(raw_name, field_name)
        except ValueError as exc:
            raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
        if not isinstance(raw_content, str):
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"{field_name}/{filename} 必须是文本",
                400,
            )
        normalized[filename] = raw_content
    return normalized


def _normalize_asset_file_collection(value: Any) -> dict[str, bytes]:
    """
    功能：校验并解码前端 JSON 上传的多媒体物料文件集合。
    入参：value（Any）：asset_files 字段，形如 {"npc/ren.png": "data:image/png;base64,..."}。
    出参：dict[str, bytes]，键为 assets/ 下相对路径，值为媒体字节。
    异常：StoryPackReleaseError；字段形状、路径、扩展名、base64 或大小非法时抛出。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StoryPackReleaseError("INVALID_ARGUMENT", "asset_files 必须是 JSON 对象", 400)
    normalized: dict[str, bytes] = {}
    for raw_name, raw_content in value.items():
        try:
            filename = normalize_relative_content_name(raw_name, "asset_files")
        except ValueError as exc:
            raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
        suffix = Path(filename).suffix.lower()
        if suffix not in STORY_PACK_ASSET_MIME_BY_EXTENSION:
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"asset_files/{filename} 扩展名非法",
                400,
            )
        if not isinstance(raw_content, str):
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"asset_files/{filename} 必须是 base64 或 data URL 字符串",
                400,
            )
        _ensure_asset_content_within_encoded_limit(filename, raw_content)
        try:
            decoded = _decode_asset_file_content(filename, raw_content)
        except ValueError as exc:
            raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
        if len(decoded) > MAX_ASSET_FILE_BYTES:
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"asset_files/{filename} 超过 {MAX_ASSET_FILE_BYTES} 字节限制",
                400,
            )
        normalized[filename] = decoded
    return normalized


def _validate_asset_files_declared(
    manifest: dict[str, Any],
    asset_files: dict[str, bytes],
) -> None:
    """
    功能：确认上传的 asset_files 均已在 manifest.assets 中声明。
    入参：manifest（dict[str, Any]）：导入请求中的 manifest 对象；
        asset_files（dict[str, bytes]）：已解码并通过路径白名单的媒体文件集合。
    出参：None。
    异常：StoryPackReleaseError；manifest.assets 形状非法或存在未声明文件时抛出。
    """
    if not asset_files:
        return
    declared_sources = _declared_asset_sources(manifest)
    for filename in asset_files:
        if filename not in declared_sources:
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"asset_files/{filename} 未在 manifest.assets 中声明",
                400,
            )


def _declared_asset_sources(manifest: dict[str, Any]) -> set[str]:
    """
    功能：读取 manifest.assets 中声明的 assets/ 相对路径集合。
    入参：manifest（dict[str, Any]）：导入请求中的 manifest 对象。
    出参：set[str]，已规范化的多媒体物料 src 集合。
    异常：StoryPackReleaseError；assets 不是对象、条目不是对象或 src 路径非法时抛出。
    """
    raw_assets = manifest.get("assets", {})
    if not isinstance(raw_assets, dict):
        raise StoryPackReleaseError("INVALID_ARGUMENT", "manifest.assets 必须是 JSON 对象", 400)
    declared: set[str] = set()
    for asset_id, raw_asset in raw_assets.items():
        if not isinstance(raw_asset, dict):
            raise StoryPackReleaseError(
                "INVALID_ARGUMENT",
                f"manifest.assets.{asset_id} 必须是 JSON 对象",
                400,
            )
        try:
            declared.add(
                normalize_relative_content_name(raw_asset.get("src"), "manifest.assets.src")
            )
        except ValueError as exc:
            raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc
    return declared


def _decode_asset_file_content(filename: str, raw_content: str) -> bytes:
    """
    功能：解码单个多媒体物料内容，支持 data URL 与纯 base64。
    入参：filename（str）：已规范化的相对文件名；raw_content（str）：上传文本。
    出参：bytes，媒体原始字节。
    异常：ValueError；媒体类型不匹配或 base64 非法时抛出。
    """
    content = raw_content.strip()
    expected_mime = STORY_PACK_ASSET_MIME_BY_EXTENSION[Path(filename).suffix.lower()]
    if content.startswith("data:"):
        header, sep, encoded = content.partition(",")
        if not sep or ";base64" not in header:
            raise ValueError(f"asset_files/{filename} data URL 必须使用 base64")
        mime_type = header.removeprefix("data:").split(";", 1)[0].strip()
        if mime_type and mime_type != expected_mime:
            raise ValueError(f"asset_files/{filename} MIME 不匹配: {mime_type} != {expected_mime}")
        content = encoded
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"asset_files/{filename} base64 非法") from exc


def _ensure_asset_content_within_encoded_limit(filename: str, raw_content: str) -> None:
    """
    功能：在实际 base64 解码前用编码长度拒绝明显超过上限的媒体文件。
    入参：filename（str）：已规范化的相对文件名；raw_content（str）：上传的 base64 或 data URL。
    出参：None。
    异常：StoryPackReleaseError；编码文本长度已不可能解码到大小限制内时抛出。
    """
    encoded = _extract_base64_payload_for_size_check(raw_content)
    # base64 每 3 字节最多展开为 4 个字符；先用当前配置的上限做保守拦截，
    # 精确字节数仍由解码后的 MAX_ASSET_FILE_BYTES 检查兜底。
    max_encoded_chars = ((MAX_ASSET_FILE_BYTES + 2) // 3) * 4
    if len(encoded) > max_encoded_chars:
        raise StoryPackReleaseError(
            "INVALID_ARGUMENT",
            f"asset_files/{filename} 超过 {MAX_ASSET_FILE_BYTES} 字节限制",
            400,
        )


def _extract_base64_payload_for_size_check(raw_content: str) -> str:
    """
    功能：提取用于大小预估的 base64 主体，避免把 data URL 头部计入媒体体积。
    入参：raw_content（str）：上传的 base64 或 data URL 字符串。
    出参：str，用于长度预检的编码主体；格式非法时返回原文本并交给正式解码报错。
    异常：无；data URL 形状与 MIME 合法性由 _decode_asset_file_content 统一处理。
    """
    content = raw_content.strip()
    if not content.startswith("data:"):
        return content
    _header, sep, encoded = content.partition(",")
    if not sep:
        return content
    return encoded


def _safe_pack_dir(root: Path, pack_id: str) -> Path:
    """
    功能：构造并校验 pack 目录路径仍位于 registry 根目录内。
    入参：root（Path）：registry 根目录；pack_id（str）：pack 目录名。
    出参：Path，未必存在的目标目录。
    异常：StoryPackReleaseError；路径越界时抛出 INVALID_ARGUMENT。
    """
    try:
        return resolve_pack_directory(root, pack_id)
    except ValueError as exc:
        raise StoryPackReleaseError("INVALID_ARGUMENT", str(exc), 400) from exc


def _write_pack_directory(
    target: Path,
    manifest: dict[str, Any],
    scenes: dict[str, dict[str, Any]],
    lore: dict[str, str],
    quests: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    asset_files: dict[str, bytes],
) -> None:
    """
    功能：按 Story Pack v0 文件结构写入 manifest、scenes、lore、quests 与 triggers。
    入参：target（Path）：目标 pack 根目录；manifest/scenes/lore/quests/triggers/asset_files：
        已规整文件集合。
    出参：None。
    异常：文件系统写入失败时抛出 OSError，由调用方清理目录。
    """
    target.mkdir(parents=True, exist_ok=False)
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_json_collection(target / "scenes", scenes)
    _write_json_collection(target / "quests", quests)
    _write_json_collection(target / "triggers", triggers)
    if asset_files:
        assets_dir = target / "assets"
        for filename, asset_content in sorted(asset_files.items(), key=lambda entry: entry[0]):
            asset_path = resolve_relative_content_path(assets_dir, filename, "asset_files")
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(asset_content)
    if lore:
        lore_dir = target / "lore"
        for filename, lore_content in lore.items():
            lore_path = resolve_relative_content_path(lore_dir, filename, "lore")
            lore_path.parent.mkdir(parents=True, exist_ok=True)
            lore_path.write_text(lore_content, encoding="utf-8")


def _write_json_collection(directory: Path, collection: dict[str, dict[str, Any]]) -> None:
    """
    功能：把按 ID 索引的 JSON 对象集合写入 `<id>.json` 文件。
    入参：directory（Path）：目标目录；collection（dict[str, dict[str, Any]]）：文件集合。
    出参：None。
    异常：文件系统写入失败时抛出 OSError。
    """
    if not collection:
        return
    directory.mkdir(parents=True, exist_ok=True)
    for item_id, item in sorted(collection.items(), key=lambda entry: entry[0]):
        resolve_json_file_path(directory, item_id, "文件 ID").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
