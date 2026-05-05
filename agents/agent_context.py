"""
Agent 运行期上下文读取工具。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("Agent.Context")
DEFAULT_AGENT_CONTEXT_DIR = Path(__file__).resolve().parents[1] / ".agent_context"
MEMORY_FILE_NAME = "MEMORY.md"
DEFAULT_MAX_MEMORY_CHARS = 4000


def load_agent_memory(
    context_dir: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_MEMORY_CHARS,
) -> str:
    """
    功能：读取 `.agent_context/MEMORY.md` 的长期剧情摘要，供 Agent prompt 挂载。
    入参：context_dir（str | Path | None，默认 None）：Agent 上下文目录；
        max_chars（int，默认 4000）：注入 prompt 的最大字符数，需为正整数。
    出参：str，过滤占位注释后的 Markdown 文本；文件缺失、为空或读取失败时返回空字符串。
    异常：内部捕获 OSError/UnicodeError 并记录 warning，按空记忆降级，避免阻断主循环。
    """
    memory_path = _resolve_memory_path(context_dir)
    if max_chars <= 0:
        logger.warning("Agent 上下文记忆上限非法，已跳过加载: max_chars=%s", max_chars)
        return ""
    if not memory_path.exists():
        logger.info("Agent 上下文记忆文件不存在，跳过加载: path=%s", memory_path)
        return ""
    try:
        raw_text = memory_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        logger.warning("Agent 上下文记忆读取失败，已降级为空: path=%s error=%s", memory_path, error)
        return ""
    memory = _strip_placeholder_comments(raw_text).strip()
    if not memory:
        logger.info("Agent 上下文记忆为空，跳过挂载: path=%s", memory_path)
        return ""
    if len(memory) > max_chars:
        memory = memory[-max_chars:]
    logger.info("Agent 上下文记忆加载成功: path=%s chars=%s", memory_path, len(memory))
    return memory


def merge_recent_memory(session_memory: str, agent_memory: str) -> str:
    """
    功能：合并 Web 会话近期记忆与 `.agent_context` 长期记忆，保持短期记忆优先。
    入参：session_memory（str）：来自会话回合表的近期摘要；
        agent_memory（str）：来自 `.agent_context/MEMORY.md` 的长期摘要。
    出参：str，合并后的 Markdown 文本；任一侧为空时返回另一侧。
    异常：不抛异常；输入按字符串处理，避免上下文合并影响回合执行。
    """
    session_text = str(session_memory or "").strip()
    agent_text = str(agent_memory or "").strip()
    if session_text and agent_text:
        # 记忆边界：会话短期摘要放前面，长期文件摘要只补充跨会话背景，避免覆盖当前回合。
        return f"## 会话近期记忆\n{session_text}\n\n## Agent长期记忆\n{agent_text}"
    return session_text or agent_text


def _resolve_memory_path(context_dir: str | Path | None) -> Path:
    """
    功能：解析 Agent 记忆文件路径，集中固定 `.agent_context/MEMORY.md` 命名。
    入参：context_dir（str | Path | None）：调用方指定目录；None 使用仓库根目录下 `.agent_context`。
    出参：Path，指向 MEMORY.md。
    异常：Path 构造异常由调用方输入类型触发并向上抛出。
    """
    base_dir = DEFAULT_AGENT_CONTEXT_DIR if context_dir is None else Path(context_dir)
    return base_dir / MEMORY_FILE_NAME


def _strip_placeholder_comments(raw_text: str) -> str:
    """
    功能：移除 Markdown HTML 注释占位行，避免把模板提示误当成剧情记忆注入 Agent。
    入参：raw_text（str）：从 MEMORY.md 读取的原始文本。
    出参：str，保留标题与真实条目后的文本。
    异常：不抛异常；非字符串输入由类型检查约束。
    """
    lines: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        lines.append(line)
    meaningful_lines = [line.strip() for line in lines if line.strip()]
    if meaningful_lines and all(line.startswith("#") for line in meaningful_lines):
        return ""
    return "\n".join(lines)
