"""
功能：解析和规范化场景媒体资源，供 Web 前端安全展示。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from tools.packs.registry import (
    story_pack_asset_media_type_for_src,
    story_pack_asset_mime_type_for_src,
)


def asset_playback_payload(value: Any) -> dict[str, Any] | None:
    """
    功能：把 Story Pack asset.playback 转为前端可消费的 JSON 对象。
    入参：value（Any）：Pydantic playback 模型、dict 或空值。
    出参：dict[str, Any] | None，存在播放策略时返回对象，否则返回 None。
    异常：不抛异常；非对象值按无播放策略降级。
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        return dumped if isinstance(dumped, dict) else None
    return None


def build_pack_asset_payloads(pack_id: str, bundle: Any) -> dict[str, dict[str, Any]]:
    """
    功能：把 Story Pack manifest.assets 转为前端可展示的只读资源映射。
    入参：pack_id（str）：当前 pack ID；bundle（Any）：StoryPackBundle 或兼容测试对象。
    出参：dict[str, dict[str, Any]]，键为 asset_id，值包含 URL、用途、媒体类型和替代文本。
    异常：不抛异常；manifest.assets 缺失或非映射时按无资源降级，单个字段缺失按空字符串降级。
    """
    assets: dict[str, dict[str, Any]] = {}
    manifest = getattr(bundle, "manifest", None)
    raw_assets = getattr(manifest, "assets", {})
    if not isinstance(raw_assets, Mapping):
        return assets
    for asset_id, asset in raw_assets.items():
        if isinstance(asset, Mapping):
            src = str(asset.get("src") or "").strip().replace("\\", "/")
            kind = str(asset.get("kind") or "")
            media_type = str(asset.get("media_type") or "")
            alt = str(asset.get("alt") or "")
            caption = str(asset.get("caption") or "")
            mime_type = str(asset.get("mime_type") or "")
            playback = asset_playback_payload(asset.get("playback"))
        else:
            src = str(getattr(asset, "src", "") or "").strip().replace("\\", "/")
            kind = str(getattr(asset, "kind", "") or "")
            media_type = str(getattr(asset, "media_type", "") or "")
            alt = str(getattr(asset, "alt", "") or "")
            caption = str(getattr(asset, "caption", "") or "")
            mime_type = str(getattr(asset, "mime_type", "") or "")
            playback = asset_playback_payload(getattr(asset, "playback", None))
        if not src:
            continue
        encoded_src = quote(src, safe="/")
        assets[str(asset_id)] = {
            "asset_id": str(asset_id),
            "kind": kind,
            "media_type": media_type or story_pack_asset_media_type_for_src(src),
            "src": src,
            "url": f"/api/story-packs/{pack_id}/assets/{encoded_src}",
            "alt": alt,
            "caption": caption,
            "mime_type": mime_type or story_pack_asset_mime_type_for_src(src),
        }
        if playback:
            assets[str(asset_id)]["playback"] = playback
    return assets


def attach_asset_urls(
    mapping: dict[str, Any],
    assets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：把单个场景对象中的 asset_id 类字段转换为对应 URL 字段。
    入参：mapping（dict[str, Any]）：场景、NPC、物品或交互对象；
        assets（dict[str, dict[str, Any]]）：manifest asset 映射。
    出参：dict[str, Any]，复制后的对象，包含 asset_url/background_asset_url 等展示字段。
    异常：不抛异常；引用缺失时保留原字段但不生成 URL。
    """
    enriched = dict(mapping)
    asset_field_to_url_field = {
        "asset_id": "asset_url",
        "background_asset_id": "background_asset_url",
        "image_asset_id": "image_asset_url",
        "portrait_asset_id": "portrait_asset_url",
        "icon_asset_id": "icon_asset_url",
    }
    for asset_field, url_field in asset_field_to_url_field.items():
        asset_id = str(enriched.get(asset_field) or "").strip()
        asset_payload = assets.get(asset_id)
        if asset_payload:
            enriched[url_field] = asset_payload.get("url")
    return enriched


def enrich_scene_snapshot_pack_assets(
    context: Any,
    scene_snapshot: dict[str, Any],
    pack_id: str | None,
) -> dict[str, Any]:
    """
    功能：按会话绑定 pack 为 scene_snapshot 注入多媒体物料 URL。
    入参：context（Any）：包含 story_pack_registry 的运行时上下文；
        scene_snapshot（dict[str, Any]）：回合场景快照；pack_id（str | None）：会话绑定 pack ID。
    出参：dict[str, Any]，复制并补全 assets、*_asset_url 字段后的快照。
    异常：不抛异常；registry 缺失、pack 缺失或字段类型不匹配时返回原快照副本。
    """
    enriched = dict(scene_snapshot)
    registry = getattr(context, "story_pack_registry", None)
    if not pack_id or registry is None:
        return enriched
    bundle = registry.get(pack_id)
    if bundle is None:
        return enriched
    assets = build_pack_asset_payloads(pack_id, bundle)
    if not assets:
        return enriched
    enriched["assets"] = assets

    current_location = enriched.get("current_location")
    if isinstance(current_location, dict):
        enriched["current_location"] = attach_asset_urls(current_location, assets)

    for list_field in ("exits", "interactables", "visible_npcs", "visible_items"):
        raw_items = enriched.get(list_field)
        if not isinstance(raw_items, list):
            continue
        enriched[list_field] = [
            attach_asset_urls(item, assets) if isinstance(item, dict) else item
            for item in raw_items
        ]

    scene_objects = enriched.get("scene_objects")
    if isinstance(scene_objects, list):
        enriched_objects: list[Any] = []
        for obj in scene_objects:
            if not isinstance(obj, dict):
                enriched_objects.append(obj)
                continue
            enriched_obj = attach_asset_urls(obj, assets)
            source_ref = enriched_obj.get("source_ref")
            if isinstance(source_ref, dict):
                enriched_obj["source_ref"] = attach_asset_urls(source_ref, assets)
            enriched_objects.append(enriched_obj)
        enriched["scene_objects"] = enriched_objects
    return enriched
