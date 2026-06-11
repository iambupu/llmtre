"""StoryPackGenerator 单元测试 —— 覆盖 Fallback 常量、降级逻辑、LLM JSON 解析与校验。"""

from __future__ import annotations

import copy

import pytest

from agents.story_pack_generator import (
    FALLBACK_LORE,
    FALLBACK_MANIFEST,
    StoryPackGenerator,
)

# ============================================================================
# 辅助函数
# ============================================================================


def _make_disabled_binding(*args: object, **kwargs: object) -> dict:
    """返回一个禁用 LLM 的绑定配置，用于 monkeypatch。"""
    return {"enabled": False, "mode": "deterministic"}


def _valid_manifest_override(**overrides: object) -> dict:
    """基于 FALLBACK_MANIFEST 构造可覆盖字段的合法 manifest。"""
    base = copy.deepcopy(FALLBACK_MANIFEST)
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _valid_generated_data(
    manifest_overrides: dict | None = None,
    lore_overrides: dict | None = None,
) -> dict:
    """构造通过 _validate_generated 的合法生成数据。"""
    manifest = _valid_manifest_override(**(manifest_overrides or {}))
    lore = copy.deepcopy(FALLBACK_LORE)
    if lore_overrides is not None:
        lore.update(lore_overrides)  # type: ignore[arg-type]
    return {"manifest": manifest, "lore": lore}


# ============================================================================
# 测试用例
# ============================================================================


class TestFallbackManifest:
    """FALLBACK_MANIFEST 常量结构测试。"""

    def test_has_all_required_keys(self) -> None:
        """FALLBACK_MANIFEST 包含所有必需字段。"""
        required = {
            "pack_id",
            "version",
            "title",
            "author",
            "description",
            "scenario_id",
            "start_scene_id",
            "supported_actions",
            "lore_files",
            "scenes",
        }
        missing = required - set(FALLBACK_MANIFEST.keys())
        assert not missing, f"缺少必需字段: {missing}"

    def test_start_scene_in_scenes(self) -> None:
        """start_scene_id 存在于 scenes 字典中。"""
        start_id = FALLBACK_MANIFEST["start_scene_id"]
        scenes = FALLBACK_MANIFEST["scenes"]
        assert isinstance(scenes, dict)
        assert start_id in scenes, f"start_scene_id='{start_id}' 不在 scenes 中"

    def test_scenes_have_required_structure(self) -> None:
        """每个 scene 包含 StoryPackSceneDef 所需字段。"""
        required_scene_fields = {
            "scene_id",
            "display_name",
            "summary",
            "exits",
            "interactables",
            "visible_npcs",
            "visible_items",
        }
        for scene_id, scene in FALLBACK_MANIFEST["scenes"].items():
            missing = required_scene_fields - set(scene.keys())
            assert not missing, f"scene '{scene_id}' 缺少字段: {missing}"


class TestFallbackLore:
    """FALLBACK_LORE 常量结构测试。"""

    def test_has_world_md(self) -> None:
        """FALLBACK_LORE 包含 world.md 键。"""
        assert "world.md" in FALLBACK_LORE
        assert isinstance(FALLBACK_LORE["world.md"], str)
        assert len(FALLBACK_LORE["world.md"]) > 0


class TestGenerateErrors:
    """generate() 异常路径测试。"""

    def test_blank_background_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空背景（含纯空白）应抛出 ValueError。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()

        with pytest.raises(ValueError, match="不能为空"):
            gen.generate(player_background="")

        with pytest.raises(ValueError, match="不能为空"):
            gen.generate(player_background="   ")

    def test_disabled_generator_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM 禁用时 generate() 返回 Fallback 结果（manifest + lore）。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()
        result = gen.generate(player_background="一个蒸汽朋克世界")

        assert isinstance(result, dict)
        assert "manifest" in result
        assert "lore" in result
        assert isinstance(result["manifest"], dict)
        assert isinstance(result["lore"], dict)
        assert "world.md" in result["lore"]


class TestValidateGenerated:
    """_validate_generated() 静态方法测试。"""

    def test_valid_data_passes(self) -> None:
        """合法数据不抛异常。"""
        data = _valid_generated_data()
        StoryPackGenerator._validate_generated(data)  # 不应抛异常

    def test_rejects_missing_manifest_field(self) -> None:
        """缺少 manifest 必需字段时 raise ValueError。"""
        data = _valid_generated_data()
        del data["manifest"]["pack_id"]  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="pack_id"):
            StoryPackGenerator._validate_generated(data)

    def test_rejects_invalid_start_scene(self) -> None:
        """start_scene_id 不在 scenes 中时 raise ValueError。"""
        data = _valid_generated_data()
        data["manifest"]["start_scene_id"] = "nonexistent_scene"  # type: ignore[index]
        with pytest.raises(ValueError, match="start_scene_id"):
            StoryPackGenerator._validate_generated(data)

    def test_rejects_non_dict_manifest(self) -> None:
        """manifest 不是 dict 时 raise ValueError。"""
        data = _valid_generated_data()
        data["manifest"] = "not_a_dict"
        with pytest.raises(ValueError, match="manifest"):
            StoryPackGenerator._validate_generated(data)

    def test_rejects_non_dict_lore(self) -> None:
        """lore 不是 dict 时 raise ValueError。"""
        data = _valid_generated_data()
        data["lore"] = None
        with pytest.raises(ValueError, match="lore"):
            StoryPackGenerator._validate_generated(data)


class TestParseLlmJson:
    """_parse_llm_json() 静态方法测试。"""

    def test_strips_code_fences(self) -> None:
        """剥离 ```json ... ``` 包装。"""
        raw = '```json\n{"key": "value"}\n```'
        result = StoryPackGenerator._parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_plain_json_passes_through(self) -> None:
        """无代码块的纯 JSON 直接解析成功。"""
        raw = '{"manifest": {"pack_id": "test"}, "lore": {"world.md": "intro"}}'
        result = StoryPackGenerator._parse_llm_json(raw)
        assert result["manifest"]["pack_id"] == "test"

    def test_fallback_finds_braces(self) -> None:
        """json.loads 失败时通过 {...} 边界查找。"""
        raw = 'Some prefix text {"key": "value"} trailing text'
        result = StoryPackGenerator._parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_handles_invalid(self) -> None:
        """完全无法解析时 raise ValueError（实现层期望空 dict 的降级此处用异常）。"""
        raw = "这完全不是 JSON"
        with pytest.raises(ValueError):
            StoryPackGenerator._parse_llm_json(raw)

    def test_trims_whitespace(self) -> None:
        """前后空白不影响解析。"""
        raw = '  \n  {"key": "value"}  \n  '
        result = StoryPackGenerator._parse_llm_json(raw)
        assert result == {"key": "value"}


class TestBuildFallbackResult:
    """_build_fallback_result() 单元测试。"""

    def test_result_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """返回结果包含 manifest 和 lore。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()
        result = gen._build_fallback_result("测试背景")

        assert "manifest" in result
        assert "lore" in result
        assert isinstance(result["manifest"], dict)
        assert isinstance(result["lore"], dict)

    def test_includes_player_background(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """world.md 中包含玩家提供的背景文本。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()
        background = "魔法与蒸汽共存的都市"
        result = gen._build_fallback_result(background)

        assert background in result["lore"]["world.md"]

    def test_manifest_is_clone_not_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """返回的 manifest 是 FALLBACK_MANIFEST 的浅拷贝，修改不影响常量。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()
        original_title = FALLBACK_MANIFEST["title"]

        result = gen._build_fallback_result("bg")
        result["manifest"]["title"] = "修改后标题"  # type: ignore[index]

        assert FALLBACK_MANIFEST["title"] == original_title
        assert result["manifest"]["title"] == "修改后标题"

    def test_lore_includes_fallback_world(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """lore 的 world.md 除背景外仍包含 Fallback 世界内容。"""
        monkeypatch.setattr(
            "agents.story_pack_generator.get_agent_model_binding",
            _make_disabled_binding,
        )
        gen = StoryPackGenerator()
        result = gen._build_fallback_result("bg")

        # FALLBACK_LORE 的 raw 内容仍然存在
        assert "TRE 系统自动生成" in result["lore"]["world.md"]
