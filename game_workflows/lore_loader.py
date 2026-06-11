"""从剧本包中加载 lore（设定）与 persona（角色）文本，注入回合 recent_memory。

本模块供内环主循环在每回合构建 SceneSnapshot 前调用，将剧本包的世界观、角色信息
注入到 GM 叙事上下文中。注入内容仅影响叙事渲染，不参与动作合法性、数值结算或状态写入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from state.contracts.story_pack import StoryPackBundle

logger = logging.getLogger(__name__)


def load_pack_context(bundle: StoryPackBundle, registry_root: str | Path) -> str:
    """功能：从剧本包中读取 lore 文件与 persona 目录，拼接为 GM 叙事上下文字符串。

    入参：
        bundle (StoryPackBundle): 已校验的剧本包，manifest.lore_files 列出需加载的设定文件。
        registry_root (str | Path): 剧本包注册表根目录，如 "story_packs"。
            函数内部自动拼接 <registry_root>/<pack_id>/ 作为 pack_root。

    出参：
        str: 拼接后的上下文字符串，每段格式为 "[Lore] <filename>\\n<content>" 或
             "[Persona] <filename>\\n<content>"，多段之间以换行分隔。
             无可加载内容时返回空字符串 ""。

    异常：
        内部捕获所有 OSError（文件缺失、权限不足等），记录 warning 后跳过该文件继续处理；
        不会向上抛出异常，保证主循环在部分 lore 不可读时仍可继续。
    """
    pack_root = Path(registry_root) / bundle.manifest.pack_id
    parts: list[str] = []

    # 阶段一：加载 Lore 设定文件
    # 优先尝试 pack_root / filename，若不存在则退到 pack_root / "lore" / filename
    # 兼容 manifest.lore_files 路径带/不带 "lore/" 前缀两种情况
    for lore_file in bundle.manifest.lore_files:
        path = pack_root / lore_file
        if not path.exists():
            path = pack_root / "lore" / lore_file
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                filename = path.name
                parts.append("[Lore] " + filename + chr(10) + content)
        except OSError as exc:
            logger.warning("Failed to read lore: %s (%s)", path, exc)

    # 阶段二：加载 persona 目录下的 .md 角色文件
    # persona 目录可选，不存在时跳过；文件按名称排序保证确定性
    persona_dir = pack_root / "persona"
    if persona_dir.exists() and persona_dir.is_dir():
        for pf in sorted(persona_dir.glob("*.md")):
            try:
                content = pf.read_text(encoding="utf-8").strip()
                if content:
                    filename = pf.name
                    parts.append("[Persona] " + filename + chr(10) + content)
            except OSError as exc:
                logger.warning("Failed to read persona: %s (%s)", pf, exc)

    if not parts:
        return ""
    return chr(10).join(parts)
