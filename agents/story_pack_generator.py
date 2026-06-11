"""
独立剧本包生成器 —— 根据玩家背景描述，调用 LLM 生成 story_pack 最小集（manifest + lore/world.md）。
LLM 不可用、超时或返回非法 JSON 时自动降级到代码内置 Fallback 剧本。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from config.agent_model_loader import get_agent_model_binding

logger = logging.getLogger("Agent.StoryPackGen")

# ---------------------------------------------------------------------------
# 内置 Fallback 最小剧本 —— LLM 不可用/超时/解析失败时的降级内容
# ---------------------------------------------------------------------------

FALLBACK_MANIFEST: dict[str, Any] = {
    "pack_id": "builtin_fallback",
    "version": "0.1.0",
    "title": "冒险之始（自动生成）",
    "author": "TRE System",
    "description": "由系统内置 Fallback 生成的默认冒险起始场景。",
    "scenario_id": "default",
    "start_scene_id": "village_entrance",
    "supported_actions": ["observe", "move", "talk", "inspect"],
    "lore_files": ["world.md"],
    "rules_overlay": {},
    "scenes": {
        "village_entrance": {
            "scene_id": "village_entrance",
            "display_name": "村口",
            "summary": "你站在一座宁静村庄的入口。石板路延伸向村落深处，远处有炊烟升起。",
            "exits": [],
            "interactables": [],
            "visible_npcs": [],
            "visible_items": [],
        },
    },
}

FALLBACK_LORE: dict[str, str] = {
    "world.md": (
        "# 世界背景\n\n"
        "这是一个由 TRE 系统自动生成的冒险世界。玩家将在未知的土地上展开旅程，"
        "探索隐藏的秘密，与各种角色相遇，并在冒险中不断成长。\n\n"
        "世界等待着第一位冒险者的到来……\n"
    ),
}

# ---------------------------------------------------------------------------
# LLM 生成 prompt 模板
# ---------------------------------------------------------------------------

_GENERATION_PROMPT_TEMPLATE = """  # noqa: E501
你是一个 TRPG 剧本设计师。请根据以下玩家提供的背景描述，生成一份完整的冒险起始剧本包。

## 玩家对世界背景的要求
{player_background}

## 输出格式要求

请 **只输出一个合法 JSON 对象**，不要包含任何 markdown 代码块标记或额外说明文字。JSON 结构如下：

{{
  "manifest": {{
    "pack_id": "generated_<简短英文标识>",
    "version": "0.1.0",
    "title": "<中文剧本标题，控制在20字以内>",
    "author": "TRE AI Generator",
    "description": "<一句话剧本描述>",
    "scenario_id": "generated",
    "start_scene_id": "<起始场景ID，英文snake_case>",
    "supported_actions": ["observe", "move", "talk", "inspect"],
    "lore_files": ["world.md"],
    "rules_overlay": {{}},
    "scenes": {{
      "<scene_id>": {{
        "scene_id": "<与start_scene_id相同>",
        "display_name": "<场景中文名>",
        "summary": "<场景描述，100字左右，营造氛围>",
        "exits": [],
        "interactables": [],
        "visible_npcs": [],
        "visible_items": []
      }}
    }}
  }},
  "lore": {{
    "world.md": "<Markdown格式的世界设定文本，200-500字，包含地理、历史、种族、魔法等设定>"
  }}
}}

## 约束
- 生成的场景和世界设定必须呼应玩家提供的背景描述。
- JSON 必须可直接解析，不要有多余逗号或注释。
- scene_id 必须与 start_scene_id 完全一致。
- lore 必须是字符串（Markdown 文本），不能是数组。
- 如果玩家背景过于简短或无法理解，生成一个通用奇幻冒险起点。"""

# ---------------------------------------------------------------------------
# StoryPackGenerator
# ---------------------------------------------------------------------------


class StoryPackGenerator:
    """独立剧本包生成器 —— 将玩家背景转化为可注册的 story_pack 最小集。

    设计意图：
    - 不与 GM Agent 共享 prompt 链或调用逻辑，完全独立的生成路径。
    - 同步调用 LLM（ollama），超时上限由绑定配置的 `timeout_seconds` 控制。
    - 文件写入留交给 API 层（web_api），本类只产出 dict。
    """

    def __init__(
        self,
        rules: dict[str, Any] | None = None,
        model_binding_key: str = "agents.story_pack_gen",
    ):
        """
        功能：初始化剧本生成器，加载 LLM 绑定配置。
        入参：rules（dict[str, Any] | None）：规则快照，当前未使用但保留扩展点。
        入参：model_binding_key（str）：Agent 绑定键，默认 `agents.story_pack_gen`。
        出参：无显式返回值；实例持有 model_binding 与 llm 启停/超时快照。
        异常：绑定项缺失时按 deterministic 降级，不中断初始化。
        """
        self._rules = rules or {}
        self.model_binding_key = model_binding_key
        self.model_binding: dict[str, Any] = get_agent_model_binding(model_binding_key)
        self.llm_enabled: bool = bool(self.model_binding.get("enabled", False))
        self.llm_config: dict[str, Any] = dict(self.model_binding.get("llm_config") or {})
        self.llm_timeout_seconds: int = int(self.model_binding.get("timeout_seconds", 30))
        self._max_retries: int = int(self.model_binding.get("max_retries", 1))

    def generate(
        self,
        prompt: str = "",
        player_background: str = "",
    ) -> dict[str, Any]:
        """
        功能：根据玩家背景描述生成剧本包数据（manifest + lore）；LLM 不可用时自动降级。
        入参：prompt（str）：自定义 LLM prompt 覆盖，为空时使用内置模板。
        入参：player_background（str）：玩家对世界背景的描述。
        出参：dict[str, Any]：包含 `manifest`（dict）和 `lore`（dict[str, str]）的字典。
        异常：player_background 为空白字符串（含纯空白）时直接 raise ValueError。
            所有其他异常（LLM 调用失败的各类网络/序列化/校验错误）均被内部捕获，
            降级返回 FALLBACK_MANIFEST + FALLBACK_LORE，不向上抛出。
        """
        trimmed = player_background.strip()
        if not trimmed:
            raise ValueError("player_background 不能为空")

        if not self.llm_enabled:
            logger.info("StoryPackGenerator LLM 未启用，使用 Fallback 剧本。")
            return self._build_fallback_result(trimmed)

        try:
            return self._call_llm_and_parse(trimmed, prompt)
        except Exception:
            logger.exception("LLM 剧本生成失败，降级到 Fallback 剧本。")
            return self._build_fallback_result(trimmed)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm_and_parse(
        self,
        player_background: str,
        custom_prompt: str = "",
    ) -> dict[str, Any]:
        """
        功能：调用 LLM（ollama /api/generate）生成 manifest + lore，解析并校验返回。
        入参：player_background（str）：玩家背景文本。
        入参：custom_prompt（str）：自定义 prompt 模板，为空时使用 _GENERATION_PROMPT_TEMPLATE。
        出参：dict[str, Any]：`{"manifest": {...}, "lore": {...}}`。
        异常：网络错误、超时、非法 JSON、必需字段缺失均向上抛出，由外层 generate() 统一捕获降级。
        """
        provider = str(self.llm_config.get("provider", "")).lower()
        if provider != "ollama":
            logger.warning(
                "StoryPackGenerator provider=%s 不受支持，降级 Fallback。",
                provider or "unknown",
            )
            raise ValueError(f"Unsupported LLM provider: {provider}")

        model = str(self.llm_config.get("model", "")).strip()
        if not model:
            raise ValueError("LLM config 缺少 model 字段")

        base_url = str(self.llm_config.get("base_url", "http://localhost:11434")).rstrip("/")
        temperature = float(self.llm_config.get("temperature", 0.7))

        template = custom_prompt.strip() or _GENERATION_PROMPT_TEMPLATE
        llm_prompt = template.replace("{player_background}", player_background)

        body: dict[str, Any] = {
            "model": model,
            "prompt": llm_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        request = urllib.request.Request(
            url=f"{base_url}/api/generate",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        # 总超时 = 配置超时 + 额外 5s 网络缓冲
        effective_timeout = self.llm_timeout_seconds + 5

        logger.info(
            "StoryPackGenerator 开始 LLM 调用：model=%s timeout=%ds",
            model,
            effective_timeout,
        )

        with urllib.request.urlopen(request, timeout=effective_timeout) as resp:
            raw = resp.read().decode("utf-8")

        data = json.loads(raw)
        llm_text = data.get("response", "").strip()

        if not llm_text:
            raise ValueError("LLM 返回空内容")

        parsed = self._parse_llm_json(llm_text)
        self._validate_generated(parsed)

        return {
            "manifest": parsed["manifest"],
            "lore": parsed["lore"],
        }

    @staticmethod
    def _parse_llm_json(text: str) -> dict[str, Any]:
        """
        功能：从 LLM 原始输出中提取并解析 JSON；自动剥离可能的 markdown 代码块包装。
        入参：text（str）：LLM 返回的原始文本。
        出参：dict[str, Any]：解析后的 JSON 字典。
        异常：无法找到合法 JSON 时 raise ValueError。
        """
        stripped = text.strip()

        # 剥离可能的 ```json ... ``` 包装
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            # 移除首行 ```json 和末行 ```
            content_lines = lines[1:] if len(lines) > 1 else lines
            if content_lines and content_lines[-1].strip() == "```":
                content_lines = content_lines[:-1]
            stripped = "\n".join(content_lines).strip()

        try:
            return json.loads(stripped)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

        # 第二次尝试：寻找第一个 { 和最后一个 }
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

        raise ValueError("LLM 输出无法解析为 JSON")

    @staticmethod
    def _validate_generated(data: dict[str, Any]) -> None:
        """
        功能：校验生成结果的结构完整性；必需字段缺失时 raise ValueError。
        入参：data（dict[str, Any]）：LLM 解析后的数据。
        出参：None，校验通过无副作用；失败时 raise ValueError。
        异常：缺失 manifest、lore、必需字段，或 start_scene_id 无对应 scene 定义。
        """
        if not isinstance(data, dict):
            raise ValueError("LLM 输出根结构不是 JSON 对象")

        manifest = data.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("缺少 manifest 字段或不是对象")

        lore = data.get("lore")
        if not isinstance(lore, dict):
            raise ValueError("缺少 lore 字段或不是对象")

        # 必需字段检查（与 Story Pack v0 manifest 契约对齐）
        required_fields = [
            "pack_id",
            "version",
            "title",
            "author",
            "scenario_id",
            "start_scene_id",
            "supported_actions",
            "lore_files",
        ]
        for field in required_fields:
            if field not in manifest:
                raise ValueError(f"manifest 缺少必需字段: {field}")

        # start_scene_id 必须有对应 scene 定义
        scenes = manifest.get("scenes", {})
        start_id = manifest.get("start_scene_id")
        if not isinstance(scenes, dict) or start_id not in scenes:
            raise ValueError(f"scenes 中缺少 start_scene_id 对应场景: {start_id}")

        # lore 必须至少有一个 world.md
        if "world.md" not in lore or not isinstance(lore["world.md"], str):
            raise ValueError("lore 缺少 world.md 或不是字符串")

    def _build_fallback_result(self, player_background: str) -> dict[str, Any]:
        """
        功能：构建 Fallback 降级结果 —— 在 FALLBACK_LORE 中填入玩家背景作为上下文。
        入参：player_background（str）：玩家背景文本。
        出参：dict[str, Any]：包含 fallback manifest 与 lore 的字典。
        """
        lore = dict(FALLBACK_LORE)
        lore["world.md"] = (
            "# 世界背景\n\n"
            f"玩家期望的世界设定：{player_background}\n\n"
            "（以下为系统自动生成的默认冒险世界）\n\n" + FALLBACK_LORE["world.md"]
        )
        return {
            "manifest": dict(FALLBACK_MANIFEST),
            "lore": lore,
        }
