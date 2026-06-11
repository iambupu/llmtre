"""
Story Pack 生成服务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents.story_pack_generator import StoryPackGenerator
from tools.packs.path_safety import resolve_json_file_path, resolve_relative_content_path
from tools.packs.registry import StoryPackRegistry

logger = logging.getLogger(__name__)


class StoryPackGenerationService:
    """
    功能：把玩家背景生成结果编译为本地 Story Pack，并通过 registry 复验后返回元数据。
    入参：registry（StoryPackRegistry）：目标剧本包注册表；
        generator_factory（Callable[[], Any] | None）：生成器工厂，默认使用 StoryPackGenerator。
    出参：StoryPackGenerationService。
    异常：初始化不读取文件，不抛业务异常。
    """

    def __init__(
        self,
        registry: StoryPackRegistry,
        generator_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        功能：保存 registry 与生成器工厂。
        入参：registry（StoryPackRegistry）：本地剧本包注册表；
            generator_factory（Callable[[], Any] | None，默认 None）：生成器构造入口，
            缺省时使用当前模块的 StoryPackGenerator。
        出参：None。
        异常：无。
        """
        self.registry = registry
        # 测试与运行时可能替换模块级 StoryPackGenerator；延迟到实例化时绑定，避免默认参数冻结旧类。
        self.generator_factory = generator_factory or StoryPackGenerator

    def generate_from_background(self, background: str) -> dict[str, Any]:
        """
        功能：根据玩家背景文本生成剧本包，持久化并注册，返回 pack 元数据。
        入参：background（str）：玩家输入的世界背景描述（非空、≤2000 字），由调用方校验。
        出参：dict[str, Any]，包含 pack_id/title/version/content_hash/scenario_id/description；
            去重命中时额外包含 duplicate=True；失败时包含 error。
        异常：文件系统写入或 registry 复验失败时清理本次生成目录，并通过 key="error" 返回。
        """
        content_hash = hashlib.sha256(background.encode("utf-8")).hexdigest()[:12]
        self.registry.refresh()
        duplicate = self._find_duplicate(content_hash)
        if duplicate is not None:
            return duplicate

        pack_dir_name = uuid.uuid4().hex[:8]
        generated = self._generate_raw(background)
        if "error" in generated:
            return generated

        manifest = dict(generated["manifest"])
        lore = dict(generated["lore"])
        manifest["pack_id"] = pack_dir_name
        manifest["version"] = manifest.get("version", "0.1.0")
        # 来源边界：背景 hash 只用于生成包去重，不替代 compiled_artifact_hash 的回放职责。
        manifest["source_background_hash"] = content_hash

        pack_root = Path(str(self.registry.root)) / pack_dir_name
        try:
            pack_root.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            logger.warning("StoryPackGenerationService 目录冲突: %s", pack_root)
            return {"error": "剧本目录冲突，请重试"}

        try:
            self._write_generated_pack(pack_root, manifest, lore)
        except ValueError as exc:
            logger.warning("StoryPackGenerationService 生成路径非法: %s", exc)
            shutil.rmtree(pack_root, ignore_errors=True)
            return {"error": f"生成的剧本包路径非法: {exc}"}
        except OSError as exc:
            logger.exception("StoryPackGenerationService 写文件失败: %s", exc)
            shutil.rmtree(pack_root, ignore_errors=True)
            return {"error": "剧本文件写入失败"}

        self.registry.refresh()
        bundle = self.registry.get(pack_dir_name)
        if bundle is None:
            diagnostics = self.registry.diagnostics().get(pack_dir_name, [])
            logger.warning(
                "StoryPackGenerationService registry 复验失败: pack=%s diagnostics=%s",
                pack_dir_name,
                diagnostics,
            )
            shutil.rmtree(pack_root, ignore_errors=True)
            self.registry.refresh()
            return {"error": "生成的剧本包未通过校验"}

        logger.info(
            "StoryPackGenerationService 已注册: pack=%s content_hash=%s",
            bundle.summary.pack_id,
            content_hash,
        )
        return {
            "pack_id": bundle.summary.pack_id,
            "title": bundle.summary.title,
            "version": bundle.summary.version,
            "content_hash": content_hash,
            "scenario_id": bundle.summary.scenario_id,
            "description": manifest.get("description", manifest.get("title", "")),
        }

    def _find_duplicate(self, content_hash: str) -> dict[str, Any] | None:
        """
        功能：查找已注册 pack 是否由同一背景文本生成。
        入参：content_hash（str）：背景内容 hash 前 12 位。
        出参：dict[str, Any] | None，命中时返回成功响应 payload，否则 None。
        异常：不抛异常；registry 摘要字段缺失时跳过该项。
        """
        for summary in self.registry.list_summaries():
            if summary.get("source_background_hash") != content_hash:
                continue
            logger.info(
                "pack 命中去重缓存: pack=%s content_hash=%s",
                summary.get("pack_id"),
                content_hash,
            )
            return {
                "pack_id": summary["pack_id"],
                "title": summary["title"],
                "version": summary["version"],
                "content_hash": content_hash,
                "scenario_id": summary.get("scenario_id", "generated"),
                "description": summary.get("title", ""),
                "duplicate": True,
            }
        return None

    def _generate_raw(self, background: str) -> dict[str, Any]:
        """
        功能：调用 StoryPackGenerator 获取 manifest/lore 原始产物。
        入参：background（str）：玩家背景文本。
        出参：dict[str, Any]，成功时包含 manifest/lore，失败时包含 error。
        异常：生成器 ValueError 转为用户可见错误；其他异常转为内部错误。
        """
        try:
            result = self.generator_factory().generate("", background)
            return {"manifest": result["manifest"], "lore": result["lore"]}
        except ValueError as exc:
            logger.warning("StoryPackGenerationService 生成校验失败: %s", exc)
            return {"error": f"剧本生成失败: {exc}"}
        except Exception:
            logger.exception("StoryPackGenerationService 未预期错误")
            return {"error": "剧本生成服务内部错误"}

    def _write_generated_pack(
        self,
        pack_root: Path,
        manifest: dict[str, Any],
        lore: dict[str, str],
    ) -> None:
        """
        功能：把生成器产出的 manifest/lore/scenes 编译成本地 Story Pack 文件结构。
        入参：pack_root（Path）：目标 pack 根目录；manifest（dict[str, Any]）：入口元数据；
            lore（dict[str, str]）：lore 文件名到内容的映射。
        出参：None。
        异常：文件系统写入失败时抛出 OSError，由调用方统一清理本次目录。
        """
        # 生成器仍以内联 scenes 作为中间结构；服务层负责拆成 v0 契约要求的独立文件。
        inline_scenes: dict[str, dict[str, Any]] = manifest.pop("scenes", {})
        with open(pack_root / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        lore_dir = pack_root / "lore"
        lore_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in lore.items():
            filepath = resolve_relative_content_path(lore_dir, filename, "lore")
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
        scenes_dir = pack_root / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        for scene_id, raw_scene in inline_scenes.items():
            scene_data = _normalize_generated_scene(scene_id, raw_scene)
            scene_path = resolve_json_file_path(scenes_dir, scene_id, "scene_id")
            with open(scene_path, "w", encoding="utf-8") as fh:
                json.dump(scene_data, fh, ensure_ascii=False, indent=2)
        if not inline_scenes:
            start_scene_id = manifest.get("start_scene_id", "village_entrance")
            fallback_scene = {
                "scene_id": start_scene_id,
                "display_name": "起始场景",
                "summary": "LLM 为你生成的冒险起点。",
                "exits": [],
                "interactables": [],
                "visible_npcs": [],
                "visible_items": [],
            }
            scene_path = resolve_json_file_path(scenes_dir, str(start_scene_id), "start_scene_id")
            with open(scene_path, "w", encoding="utf-8") as fh:
                json.dump(fallback_scene, fh, ensure_ascii=False, indent=2)


def _normalize_generated_scene(scene_id: str, raw_scene: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把生成器旧字段场景映射为 StoryPackSceneDef 契约字段。
    入参：scene_id（str）：场景 ID；raw_scene（dict[str, Any]）：生成器场景原始数据。
    出参：dict[str, Any]，包含 scene_id/display_name/summary/exits/interactables/visible_*。
    异常：不抛异常；缺失字段使用安全默认值。
    """
    return {
        "scene_id": scene_id,
        "display_name": raw_scene.get("display_name") or raw_scene.get("name", "未命名场景"),
        "summary": raw_scene.get("summary") or raw_scene.get("description", ""),
        "exits": raw_scene.get("exits", []),
        "interactables": raw_scene.get("interactables", []),
        "visible_npcs": raw_scene.get("visible_npcs") or raw_scene.get("npcs", []),
        "visible_items": (
            raw_scene.get("visible_items") or raw_scene.get("objects") or raw_scene.get("items", [])
        ),
    }
