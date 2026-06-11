"""
Agent 运行期上下文读取工具。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger("Agent.Context")
DEFAULT_AGENT_CONTEXT_DIR = Path(__file__).resolve().parents[1] / ".agent_context"
MEMORY_FILE_NAME = "MEMORY.md"
SESSION_MEMORY_DIR_NAME = "sessions"
DEFAULT_MAX_MEMORY_CHARS = 4000


def load_agent_memory(
    context_dir: str | Path | None = None,
    session_id: str | None = None,
    max_chars: int = DEFAULT_MAX_MEMORY_CHARS,
) -> str:
    """
    功能：读取 Agent 长期剧情摘要，优先支持会话级 `.agent_context/sessions/<session_id>/MEMORY.md`。
    入参：context_dir（str | Path | None，默认 None）：Agent 上下文目录；
        session_id（str | None，默认 None）：游戏会话 ID；为空时读取兼容的全局 MEMORY.md；
        max_chars（int，默认 4000）：注入 prompt 的最大字符数，需为正整数。
    出参：str，过滤占位注释后的 Markdown 文本；文件缺失、为空或读取失败时返回空字符串。
    异常：内部捕获路径校验、OSError/UnicodeError 并记录 warning，按空记忆降级。
    """
    try:
        memory_path = _resolve_memory_path(context_dir, session_id=session_id)
    except ValueError as error:
        logger.warning("Agent 上下文记忆路径非法，已跳过加载: error=%s", error)
        return ""
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


def initialize_session_agent_memory(
    session_id: str,
    context_dir: str | Path | None = None,
) -> Path | None:
    """
    功能：为新游戏会话创建独立长期记忆文件空模板。
    入参：session_id（str）：游戏会话 ID；context_dir（str | Path | None，默认 None）：上下文目录。
    出参：Path | None，成功返回会话 MEMORY.md 路径；写入失败时返回 None。
    异常：内部捕获路径校验、OSError/UnicodeError，记录 warning 后降级，不阻断会话创建。
    """
    return write_session_agent_memory(
        session_id=session_id,
        memory_summary="",
        context_dir=context_dir,
        long_term_memory="",
    )


def write_session_agent_memory(
    session_id: str,
    memory_summary: str,
    context_dir: str | Path | None = None,
    long_term_memory: str | None = None,
) -> Path | None:
    """
    功能：把数据库中的会话长期叙事记忆镜像写入该会话自己的 MEMORY.md。
    入参：session_id（str）：游戏会话 ID；memory_summary（str）：会话摘要文本；
        context_dir（str | Path | None，默认 None）：Agent 上下文目录；
        long_term_memory（str | None，默认 None）：结构化长期记忆导出的 GM 上下文；
        None 表示兼容旧摘要镜像，非 None 表示按长期记忆镜像渲染。
    出参：Path | None，成功返回 `.agent_context/sessions/<session_id>/MEMORY.md`；失败返回 None。
    异常：内部捕获路径校验、OSError/UnicodeError，记录 warning 后降级，不影响权威 DB 状态。
    """
    try:
        memory_path = _resolve_memory_path(context_dir, session_id=session_id)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(
            _render_session_memory_file(
                memory_summary=memory_summary,
                long_term_memory=long_term_memory,
            ),
            encoding="utf-8",
        )
    except (OSError, UnicodeError, ValueError) as error:
        logger.warning(
            "Agent 会话长期记忆写入失败，已保留数据库记忆为权威来源: session_id=%s error=%s",
            session_id,
            error,
        )
        return None
    logger.info("Agent 会话长期记忆已同步: session_id=%s path=%s", session_id, memory_path)
    return memory_path


def delete_session_agent_memory(
    session_id: str,
    context_dir: str | Path | None = None,
) -> Path | None:
    """
    功能：删除指定会话的 Agent 长期记忆目录，供会话数据删除流程清理文件镜像。
    入参：session_id（str）：游戏会话 ID；context_dir（str | Path | None，默认 None）：上下文目录。
    出参：Path | None，成功删除或目录原本不存在时返回会话目录路径；路径非法或删除失败返回 None。
    异常：内部捕获路径校验与 OSError，记录 warning 后降级，避免文件清理失败回滚权威 DB 删除。
    """
    try:
        memory_dir = _resolve_memory_path(context_dir, session_id=session_id).parent
        if not memory_dir.exists():
            logger.info("Agent 会话长期记忆目录不存在，跳过删除: session_id=%s", session_id)
            return memory_dir
        # 文件系统镜像不是权威状态源；DB 删除成功后这里按 best-effort 清理，失败只记录日志。
        shutil.rmtree(memory_dir)
    except (OSError, ValueError) as error:
        logger.warning(
            "Agent 会话长期记忆删除失败，数据库删除结果保持不变: session_id=%s error=%s",
            session_id,
            error,
        )
        return None
    logger.info("Agent 会话长期记忆已删除: session_id=%s path=%s", session_id, memory_dir)
    return memory_dir


def merge_recent_memory(session_memory: str, agent_memory: str) -> str:
    """
    功能：合并 Web 会话近期记忆与 `.agent_context` 长期记忆，保持短期记忆优先。
    入参：session_memory（str）：来自会话回合表的近期摘要；
        agent_memory（str）：来自会话级
        `.agent_context/sessions/<session_id>/MEMORY.md` 的长期摘要。
    出参：str，合并后的 Markdown 文本；任一侧为空时返回另一侧。
    异常：不抛异常；输入按字符串处理，避免上下文合并影响回合执行。
    """
    session_text = str(session_memory or "").strip()
    agent_text = str(agent_memory or "").strip()
    if session_text and agent_text:
        # 记忆边界：会话短期摘要放前面，长期文件摘要只补充同会话背景，避免覆盖当前回合。
        return f"## 会话近期记忆\n{session_text}\n\n## Agent长期记忆\n{agent_text}"
    return session_text or agent_text


def _resolve_memory_path(context_dir: str | Path | None, session_id: str | None = None) -> Path:
    """
    功能：解析 Agent 记忆文件路径，集中固定全局与会话级 MEMORY.md 命名。
    入参：context_dir（str | Path | None）：调用方指定目录；None 使用仓库根目录下 `.agent_context`；
        session_id（str | None，默认 None）：存在时解析到 `sessions/<session_id>/MEMORY.md`。
    出参：Path，指向 MEMORY.md。
    异常：ValueError；session_id 含路径分隔符或非法字符时抛出，阻断目录逃逸。
    """
    base_dir = DEFAULT_AGENT_CONTEXT_DIR if context_dir is None else Path(context_dir)
    if session_id is not None:
        normalized_session_id = _normalize_session_id_for_path(session_id)
        return base_dir / SESSION_MEMORY_DIR_NAME / normalized_session_id / MEMORY_FILE_NAME
    return base_dir / MEMORY_FILE_NAME


def _normalize_session_id_for_path(session_id: str) -> str:
    """
    功能：校验并规整用于文件路径的会话 ID，避免越界写入。
    入参：session_id（str）：来自 Web 会话的稳定 ID。
    出参：str，可安全作为单级目录名的会话 ID。
    异常：ValueError；为空、过长或包含非 `[a-zA-Z0-9_-]` 字符时抛出。
    """
    normalized = str(session_id or "").strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not normalized or len(normalized) > 64 or any(char not in allowed for char in normalized):
        raise ValueError("session_id 不适合作为 Agent 记忆目录名")
    return normalized


def _render_session_memory_file(
    memory_summary: str,
    long_term_memory: str | None = None,
) -> str:
    """
    功能：把会话长期记忆渲染为 Markdown 文件内容。
    入参：memory_summary（str）：兼容路径下的数据库短期摘要；
        long_term_memory（str | None，默认 None）：结构化长期记忆导出的上下文。
    出参：str，空内容返回仅含标题和占位注释的模板。
    异常：无；输入按字符串处理。
    """
    long_term = str(long_term_memory or "").strip() if long_term_memory is not None else None
    if long_term_memory is not None:
        if not long_term:
            return (
                "# 会话长期记忆\n\n"
                "## 长期叙事记忆\n"
                "<!-- 有效剧情推进后自动写入该会话的长期叙事记忆 -->\n"
            )
        return f"# 会话长期记忆\n\n{long_term}\n"

    summary = str(memory_summary or "").strip()
    if not summary:
        return (
            "# 会话长期记忆\n\n" "## 摘要\n" "<!-- 有效剧情推进后自动写入该会话的长期记忆摘要 -->\n"
        )
    return f"# 会话长期记忆\n\n## 摘要\n{summary}\n"


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
