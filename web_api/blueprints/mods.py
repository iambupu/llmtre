"""
功能：提供 MOD 媒体资源读取相关 Flask 路由。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from flask import Blueprint, Response, send_file

from tools.mod_media import (
    declared_media_by_path,
    media_mime_type_for_src,
    normalize_mod_media_path,
)
from web_api.service import (
    MODS_ROOT,
    REGISTRY_PATH,
    error,
    logger,
    success,
    validate_character_id,
)

mods_blueprint = Blueprint("mods", __name__, url_prefix="/api/mods")


def _load_mod_registry() -> dict[str, Any]:
    """
    功能：读取 MOD 注册表供只读媒体 API 使用。
    入参：无，路径来自 web_api.service.REGISTRY_PATH。
    出参：dict[str, Any]，读取失败或结构非法时返回空对象。
    异常：内部捕获 YAML/IO 异常并记录日志，避免媒体 API 阻断 Web 服务。
    """
    registry_path = Path(REGISTRY_PATH)
    if not registry_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("读取 MOD 注册表失败，媒体 API 降级为空: %s", str(exc))
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _enabled_mod_entry(registry: dict[str, Any], mod_id: str) -> dict[str, Any] | None:
    """
    功能：从注册表中查找已启用的 MOD 条目。
    入参：registry（dict）：mod_registry.yml 对象；mod_id（str）：目标 MOD ID。
    出参：dict[str, Any] | None，命中 enabled=true 条目时返回，否则 None。
    异常：不抛异常；脏注册表结构会被跳过。
    """
    active_mods = registry.get("active_mods", [])
    if not isinstance(active_mods, list):
        return None
    for item in active_mods:
        if not isinstance(item, dict):
            continue
        if str(item.get("mod_id") or "") == mod_id and bool(item.get("enabled", False)):
            return item
    return None


def _iter_enabled_mod_media(
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    功能：遍历已启用 MOD 的已校验媒体清单，并生成前端可直接使用的 URL。
    入参：registry（dict）：mod_registry.yml 对象。
    出参：tuple[list[dict[str, Any]], dict[str, Any]]，分别为媒体列表和诊断映射。
    异常：不抛异常；缺失或非法媒体条目会被跳过。
    """
    media_items: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    active_mods = registry.get("active_mods", [])
    if not isinstance(active_mods, list):
        return media_items, diagnostics
    for mod in active_mods:
        if not isinstance(mod, dict) or not bool(mod.get("enabled", False)):
            continue
        mod_id = str(mod.get("mod_id") or "").strip()
        media_manifest = mod.get("media_manifest", {})
        if mod.get("media_diagnostics"):
            diagnostics[mod_id] = mod.get("media_diagnostics")
        if not mod_id or not isinstance(media_manifest, dict):
            continue
        for media_id, media in media_manifest.items():
            if not isinstance(media, dict):
                continue
            src = str(media.get("src") or "").strip()
            if not src:
                continue
            payload = dict(media)
            payload["mod_id"] = mod_id
            payload["media_id"] = str(media.get("media_id") or media_id)
            payload["url"] = f"/api/mods/{mod_id}/media/{quote(src, safe='/')}"
            media_items.append(payload)
    return media_items, diagnostics


@mods_blueprint.get("/media")
def list_mod_media() -> tuple[Any, int]:
    """
    功能：列出已启用 MOD 中通过扫描校验的媒体物料。
    入参：无。
    出参：tuple[Any, int]，包含 media 列表和 diagnostics。
    异常：注册表读取失败在 _load_mod_registry 内降级为空列表。
    """
    registry = _load_mod_registry()
    media_items, diagnostics = _iter_enabled_mod_media(registry)
    logger.info(
        "list_mod_media 查询成功: media=%s diagnostics=%s", len(media_items), len(diagnostics)
    )
    return success({"media": media_items, "diagnostics": diagnostics})


@mods_blueprint.get("/<mod_id>/media/<path:media_path>")
def get_mod_media(mod_id: str, media_path: str) -> Response | tuple[Any, int]:
    """
    功能：读取已启用 MOD 中 media_manifest 声明的媒体文件。
    入参：mod_id（path）：目标 MOD ID；media_path（path）：assets/ 下相对媒体路径。
    出参：Response | tuple[Any, int]，命中返回媒体文件，失败返回统一错误 envelope。
    异常：send_file 文件读取异常由 Flask 处理；路径非法返回 INVALID_ARGUMENT。
    """
    if not validate_character_id(mod_id):
        logger.warning("get_mod_media 参数非法: mod_id=%s", mod_id)
        return error("INVALID_ARGUMENT", "mod_id 格式非法", 400)
    try:
        normalized_media_path = normalize_mod_media_path(media_path)
    except ValueError as exc:
        logger.warning("get_mod_media 路径非法: mod_id=%s media=%s", mod_id, media_path)
        return error("INVALID_ARGUMENT", str(exc), 400)

    registry = _load_mod_registry()
    mod_entry = _enabled_mod_entry(registry, mod_id)
    if mod_entry is None:
        logger.warning("get_mod_media MOD 不存在或未启用: mod_id=%s", mod_id)
        return error("MOD_NOT_FOUND", "MOD 不存在或未启用", 404)

    media_manifest = mod_entry.get("media_manifest", {})
    if not isinstance(media_manifest, dict):
        return error("MEDIA_NOT_FOUND", "media 不存在或未声明", 404)
    media = declared_media_by_path(media_manifest, normalized_media_path)
    if media is None:
        logger.warning(
            "get_mod_media 未声明媒体: mod_id=%s media=%s",
            mod_id,
            normalized_media_path,
        )
        return error("MEDIA_NOT_FOUND", "media 不存在或未声明", 404)

    media_root = (Path(MODS_ROOT) / mod_id / "assets").resolve()
    media_file = (media_root / normalized_media_path).resolve()
    try:
        media_file.relative_to(media_root)
    except ValueError:
        logger.warning("get_mod_media 解析越界: %s", media_file)
        return error("INVALID_ARGUMENT", "media 路径非法", 400)
    if not media_file.exists() or not media_file.is_file():
        logger.warning("get_mod_media 文件缺失: %s", media_file)
        return error("MEDIA_NOT_FOUND", "media 文件不存在", 404)

    mime_type = str(media.get("mime_type") or "") or media_mime_type_for_src(normalized_media_path)
    logger.info("get_mod_media 查询成功: mod_id=%s media=%s", mod_id, normalized_media_path)
    # 视频/音频需要 Range 请求支持；conditional=True 让 Flask 处理浏览器拖动播放进度。
    return send_file(media_file, mimetype=mime_type, conditional=True)
