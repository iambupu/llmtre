"""
功能：提供剧本包列表、导入、详情与删除相关 Flask 路由。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, request, send_file

from tools.packs.generation_service import StoryPackGenerationService
from tools.packs.path_safety import normalize_relative_content_name
from tools.packs.registry import story_pack_asset_mime_type_for_src
from tools.packs.release_manager import (
    StoryPackReleaseError,
    delete_story_pack,
    import_story_pack_payload,
)
from web_api.service import error, get_runtime_context, logger, success, validate_character_id

story_packs_blueprint = Blueprint("story_packs", __name__, url_prefix="/api/story-packs")


def _declared_asset_by_path(bundle: Any, asset_path: str) -> Any | None:
    """
    功能：按 assets/ 下相对路径查找 manifest 中已声明的多媒体物料。
    入参：bundle（Any）：StoryPackBundle；asset_path（str）：URL 中的相对资源路径。
    出参：Any | None，命中返回 StoryPackAssetDef，否则返回 None。
    异常：不抛异常；非法路径由调用方先规范化。
    """
    for asset in bundle.manifest.assets.values():
        try:
            normalized_src = normalize_relative_content_name(asset.src, "assets")
        except ValueError:
            continue
        if normalized_src == asset_path:
            return asset
    return None


@story_packs_blueprint.get("")
def list_story_packs() -> tuple[Any, int]:
    """
    功能：列出本地已校验通过的 A2 Story Pack 摘要。
    入参：无。
    出参：tuple[Any, int]，返回 packs 与 diagnostics。
    异常：registry 缺失时由 get_runtime_context 抛出 RuntimeError 并交给 Flask 处理。
    """
    context = get_runtime_context()
    context.story_pack_registry.refresh()
    packs = context.story_pack_registry.list_summaries()
    diagnostics = context.story_pack_registry.diagnostics()
    logger.info("list_story_packs 查询成功: valid=%s invalid=%s", len(packs), len(diagnostics))
    return success({"packs": packs, "diagnostics": diagnostics})


@story_packs_blueprint.get("/<pack_id>")
def get_story_pack(pack_id: str) -> tuple[Any, int]:
    """
    功能：读取单个 Story Pack 摘要与场景预览。
    入参：pack_id（path）：Story Pack 稳定 ID。
    出参：tuple[Any, int]，存在返回 manifest/summary/scenes，缺失返回 PACK_NOT_FOUND。
    异常：参数非法返回 INVALID_ARGUMENT。
    """
    if not validate_character_id(pack_id):
        logger.warning("get_story_pack 参数非法: pack_id=%s", pack_id)
        return error("INVALID_ARGUMENT", "pack_id 格式非法", 400)
    context = get_runtime_context()
    context.story_pack_registry.refresh()
    bundle = context.story_pack_registry.get(pack_id)
    if bundle is None:
        logger.warning("get_story_pack 剧本包不存在: pack_id=%s", pack_id)
        return error("PACK_NOT_FOUND", "pack_id 不存在或未通过校验", 404)
    # A2-Core 预览只返回已校验的只读内容，不提供上传、删除或运行时触发器写入能力。
    scenes = [
        scene.model_dump()
        for _scene_id, scene in sorted(bundle.scenes.items(), key=lambda item: item[0])
    ]
    return success(
        {
            "summary": bundle.summary.model_dump(),
            "manifest": bundle.manifest.model_dump(),
            "scenes": scenes,
        }
    )


@story_packs_blueprint.get("/<pack_id>/assets/<path:asset_path>")
def get_story_pack_asset(pack_id: str, asset_path: str) -> Response | tuple[Any, int]:
    """
    功能：读取已校验 Story Pack 中 manifest 声明的多媒体物料。
    入参：pack_id（path）：Story Pack 稳定 ID；asset_path（path）：assets/ 下相对路径。
    出参：Response | tuple[Any, int]，命中返回媒体文件，非法或不存在返回统一错误 envelope。
    异常：文件系统读取异常由 Flask/send_file 处理；路径非法返回 INVALID_ARGUMENT。
    """
    if not validate_character_id(pack_id):
        logger.warning("get_story_pack_asset 参数非法: pack_id=%s", pack_id)
        return error("INVALID_ARGUMENT", "pack_id 格式非法", 400)
    try:
        normalized_asset_path = normalize_relative_content_name(asset_path, "assets")
    except ValueError as exc:
        logger.warning("get_story_pack_asset 路径非法: pack_id=%s asset=%s", pack_id, asset_path)
        return error("INVALID_ARGUMENT", str(exc), 400)

    context = get_runtime_context()
    # 浏览器会对媒体文件发起缓存探测或 Range 请求；这里复用启动、列表、
    # 导入、删除或建会话阶段刷新的 registry，避免每个片段请求触发全量扫描和哈希。
    bundle = context.story_pack_registry.get(pack_id)
    if bundle is None:
        logger.warning("get_story_pack_asset 剧本包不存在: pack_id=%s", pack_id)
        return error("PACK_NOT_FOUND", "pack_id 不存在或未通过校验", 404)
    asset = _declared_asset_by_path(bundle, normalized_asset_path)
    if asset is None:
        logger.warning(
            "get_story_pack_asset 未声明资源: pack_id=%s asset=%s",
            pack_id,
            normalized_asset_path,
        )
        return error("ASSET_NOT_FOUND", "asset 不存在或未声明", 404)

    asset_file = (
        Path(str(context.story_pack_registry.root)) / pack_id / "assets" / normalized_asset_path
    ).resolve()
    asset_root = (Path(str(context.story_pack_registry.root)) / pack_id / "assets").resolve()
    try:
        asset_file.relative_to(asset_root)
    except ValueError:
        logger.warning("get_story_pack_asset 解析越界: %s", asset_file)
        return error("INVALID_ARGUMENT", "asset 路径非法", 400)
    if not asset_file.exists() or not asset_file.is_file():
        logger.warning("get_story_pack_asset 文件缺失: %s", asset_file)
        return error("ASSET_NOT_FOUND", "asset 文件不存在", 404)

    mime_type = (
        asset.mime_type
        or story_pack_asset_mime_type_for_src(normalized_asset_path)
        or mimetypes.guess_type(asset_file.name)[0]
    )
    logger.info(
        "get_story_pack_asset 查询成功: pack_id=%s asset=%s",
        pack_id,
        normalized_asset_path,
    )
    return send_file(asset_file, mimetype=mime_type, conditional=True)


@story_packs_blueprint.post("")
def import_story_pack() -> tuple[Any, int]:
    """
    功能：导入前端上传的 Story Pack JSON 文件集合，落盘后立即通过 registry 复验。
    入参：无显式入参；从 JSON body 读取 manifest、scenes，可选 lore、quests、triggers。
    出参：tuple[Any, int]，成功返回 summary，失败返回统一错误 envelope。
    异常：StoryPackReleaseError 转为业务错误；未预期文件系统异常由 Flask 全局错误处理。
    """
    body: dict[str, Any] = request.get_json(silent=True) or {}
    context = get_runtime_context()
    try:
        result = import_story_pack_payload(context.story_pack_registry, body)
    except StoryPackReleaseError as exc:
        logger.warning("import_story_pack 失败: code=%s message=%s", exc.code, exc)
        return error(exc.code, str(exc), exc.status_code)
    logger.info("import_story_pack 导入成功: pack_id=%s", result["summary"].get("pack_id"))
    return success(result, 201)


@story_packs_blueprint.delete("/<pack_id>")
def delete_story_pack_route(pack_id: str) -> tuple[Any, int]:
    """
    功能：删除本地非官方 Story Pack，不删除任何历史 session。
    入参：pack_id（path）：目标 Story Pack ID。
    出参：tuple[Any, int]，成功返回 deleted_pack_id，失败返回统一错误 envelope。
    异常：StoryPackReleaseError 转为业务错误；删除时文件系统异常由 Flask 全局错误处理。
    """
    if not validate_character_id(pack_id):
        logger.warning("delete_story_pack 参数非法: pack_id=%s", pack_id)
        return error("INVALID_ARGUMENT", "pack_id 格式非法", 400)
    context = get_runtime_context()
    try:
        result = delete_story_pack(context.story_pack_registry, pack_id)
    except StoryPackReleaseError as exc:
        logger.warning("delete_story_pack 失败: code=%s message=%s", exc.code, exc)
        return error(exc.code, str(exc), exc.status_code)
    logger.info("delete_story_pack 删除成功: pack_id=%s", pack_id)
    return success(result)


@story_packs_blueprint.post("/generate")
def generate_story_pack() -> tuple[Any, int]:
    """
    功能：接收玩家背景描述，调用 StoryPackGenerationService 生成剧本包并返回元数据。
    入参：无显式入参；从 request JSON body 读取 `background` 字符串。
    出参：tuple[Any, int]，成功返回 pack_id/title/content_hash 等元数据，失败返回具体错误码。
    异常：background 缺失/空字符串返回 INVALID_ARGUMENT 400；LLM 不可用时自动降级仍可成功；
          文件系统写入失败返回 SERVER_ERROR 500。
    """
    body: dict[str, Any] = request.get_json(silent=True) or {}
    background: str = (body.get("background") or "").strip()
    if not background:
        logger.warning("generate_story_pack 缺少 background 字段")
        return error("INVALID_ARGUMENT", "缺少 background 字段，内容不能为空", 400)
    if len(background) > 2000:
        logger.warning("generate_story_pack background 超长: len=%s", len(background))
        return error("INVALID_ARGUMENT", "background 内容过长，限制 2000 字符", 400)

    context = get_runtime_context()
    result = StoryPackGenerationService(context.story_pack_registry).generate_from_background(
        background
    )
    if "error" in result:
        return error("SERVER_ERROR", result["error"], 500)
    return success(result)
