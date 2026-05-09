from __future__ import annotations

from typing import Any

from flask import Blueprint

from web_api.service import error, get_runtime_context, logger, success, validate_character_id

story_packs_blueprint = Blueprint("story_packs", __name__, url_prefix="/api/story-packs")


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
