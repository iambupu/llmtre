"""
MOD 媒体物料校验与序列化工具。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from state.contracts.media import MediaPlaybackPolicy
from tools.packs.path_safety import (
    normalize_relative_content_name,
    resolve_relative_content_path,
    validate_file_identifier,
)

MOD_MEDIA_MIME_BY_EXTENSION: dict[str, str] = {
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
MOD_MEDIA_EXTENSIONS_BY_KIND: dict[str, set[str]] = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "gif": {".gif"},
    "video": {".mp4", ".webm", ".ogv", ".mov"},
    "audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac"},
}
MOD_MEDIA_KINDS = frozenset(MOD_MEDIA_EXTENSIONS_BY_KIND)


def media_mime_type_for_src(src: str) -> str:
    """
    功能：按媒体文件扩展名推断稳定 MIME 类型。
    入参：src（str）：相对 assets/ 的媒体文件路径。
    出参：str，命中白名单时返回固定 MIME，否则使用 mimetypes 兜底或 application/octet-stream。
    异常：不抛异常；未知后缀走兜底类型。
    """
    suffix = Path(src).suffix.lower()
    return (
        MOD_MEDIA_MIME_BY_EXTENSION.get(suffix)
        or mimetypes.guess_type(src)[0]
        or "application/octet-stream"
    )


def normalize_mod_media_manifest(
    raw_manifest: Any,
    mod_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """
    功能：校验并规范化 MOD 的 media_manifest。
    入参：raw_manifest（Any）：mod_info.json 中的 media_manifest 字段；
        mod_root（Path）：当前 MOD 根目录，媒体文件必须位于其 assets/ 子目录内。
    出参：tuple[dict[str, dict[str, Any]], list[str]]，分别为可服务媒体清单与诊断文本。
    异常：不抛业务异常；单条媒体非法时追加 diagnostics 并跳过。
    """
    if raw_manifest in (None, {}):
        return {}, []
    if not isinstance(raw_manifest, dict):
        return {}, ["media_manifest 必须是对象映射"]

    media_manifest: dict[str, dict[str, Any]] = {}
    diagnostics: list[str] = []
    assets_root = mod_root / "assets"
    for raw_media_id, raw_media in raw_manifest.items():
        try:
            media_id = validate_file_identifier(str(raw_media_id), "media_id")
            if not isinstance(raw_media, dict):
                raise ValueError(f"media_manifest.{media_id} 必须是对象")
            media_manifest[media_id] = _normalize_single_media(media_id, raw_media, assets_root)
        except ValueError as exc:
            diagnostics.append(str(exc))
    return media_manifest, diagnostics


def normalize_mod_media_path(raw_path: Any) -> str:
    """
    功能：规范化 URL 或 manifest 中的媒体路径，保持与 assets/ 目录边界一致。
    入参：raw_path（Any）：相对 assets/ 的路径。
    出参：str，使用 `/` 分隔的安全相对路径。
    异常：ValueError；路径为空、绝对路径、含盘符或越界片段时抛出。
    """
    return normalize_relative_content_name(raw_path, "media")


def declared_media_by_path(
    media_manifest: dict[str, Any],
    media_path: str,
) -> dict[str, Any] | None:
    """
    功能：按相对路径从已规范化媒体清单中查找声明项。
    入参：media_manifest（dict[str, Any]）：注册表中的 media_manifest；
        media_path（str）：已规范化的相对 assets/ 路径。
    出参：dict[str, Any] | None，命中时返回媒体声明，否则返回 None。
    异常：不抛异常；脏数据条目会被跳过。
    """
    for media in media_manifest.values():
        if not isinstance(media, dict):
            continue
        try:
            normalized_src = normalize_mod_media_path(media.get("src"))
        except ValueError:
            continue
        if normalized_src == media_path:
            return media
    return None


def _normalize_single_media(
    media_id: str,
    raw_media: dict[str, Any],
    assets_root: Path,
) -> dict[str, Any]:
    """
    功能：校验单条媒体声明并转换为注册表可持久化结构。
    入参：media_id（str）：媒体稳定 ID；raw_media（dict[str, Any]）：原始声明；
        assets_root（Path）：当前 MOD 的 assets 根目录。
    出参：dict[str, Any]，包含 media_id、kind、src、mime_type、size_bytes 等字段。
    异常：ValueError；kind、路径、扩展名、MIME 或文件存在性不符合契约时抛出。
    """
    kind = str(raw_media.get("kind") or "").strip().lower()
    if kind not in MOD_MEDIA_KINDS:
        raise ValueError(f"media_manifest.{media_id}.kind 必须为 image/gif/video/audio")

    src = normalize_mod_media_path(raw_media.get("src"))
    suffix = Path(src).suffix.lower()
    allowed_extensions = MOD_MEDIA_EXTENSIONS_BY_KIND[kind]
    if suffix not in allowed_extensions:
        raise ValueError(
            f"media_manifest.{media_id}.src 扩展名 {suffix or '<none>'} 与 kind={kind} 不匹配"
        )

    media_file = resolve_relative_content_path(assets_root, src, f"media_manifest.{media_id}.src")
    if not media_file.exists() or not media_file.is_file():
        raise ValueError(f"media_manifest.{media_id}.src 文件不存在: assets/{src}")

    inferred_mime = media_mime_type_for_src(src)
    raw_mime_type = str(raw_media.get("mime_type") or "").strip().lower()
    if raw_mime_type and raw_mime_type != inferred_mime:
        mismatch = f"{raw_mime_type} != {inferred_mime}"
        raise ValueError(f"media_manifest.{media_id}.mime_type 与扩展名不匹配: {mismatch}")

    payload: dict[str, Any] = {
        "media_id": media_id,
        "kind": kind,
        "src": src,
        "mime_type": raw_mime_type or inferred_mime,
        "size_bytes": media_file.stat().st_size,
    }
    playback = _normalize_media_playback_policy(media_id, raw_media.get("playback"))
    if playback:
        payload["playback"] = playback
    for optional_key in ("label", "alt", "caption"):
        optional_value = raw_media.get(optional_key)
        if isinstance(optional_value, str) and optional_value.strip():
            payload[optional_key] = optional_value.strip()
    return payload


def _normalize_media_playback_policy(
    media_id: str,
    raw_playback: Any,
) -> dict[str, Any] | None:
    """
    功能：校验并序列化 MOD 媒体播放生命周期策略。
    入参：media_id（str）：媒体稳定 ID；raw_playback（Any）：media_manifest.<id>.playback 原始值。
    出参：dict[str, Any] | None，存在合法播放策略时返回 JSON 对象，否则返回 None。
    异常：ValueError；playback 不是对象或字段不符合 MediaPlaybackPolicy 时抛出。
    """
    if raw_playback in (None, {}):
        return None
    if not isinstance(raw_playback, dict):
        raise ValueError(f"media_manifest.{media_id}.playback 必须是对象")
    try:
        policy = MediaPlaybackPolicy.model_validate(raw_playback)
    except ValidationError as exc:
        messages = "; ".join(
            ".".join(str(part) for part in item.get("loc", ())) or str(item.get("type"))
            for item in exc.errors()
        )
        raise ValueError(f"media_manifest.{media_id}.playback 非法: {messages}") from exc
    return policy.model_dump(mode="json", exclude_none=True)
