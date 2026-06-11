"""
游戏动作文本规范化工具。
"""

from __future__ import annotations

MOVE_LABEL_PREFIXES = (
    "前往",
    "走向",
    "进入",
    "回到",
    "返回",
    "沿",
    "穿过",
    "从",
    "带着",
    "去",
    "到",
)


def build_move_action_text(label: str) -> str:
    """
    功能：把出口标签转换为玩家可直接提交的移动动作文本。
    入参：label（str）：剧本或场景快照提供的出口展示名，允许已包含移动动词。
    出参：str，适合按钮、quick action 和失败提示复用的中文移动文本。
    异常：不抛异常；空白 label 会降级为“前往该方向”。
    """
    destination = label.strip() or "该方向"
    if destination.startswith(MOVE_LABEL_PREFIXES):
        return destination
    return f"前往{destination}"
