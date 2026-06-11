"""
Story Pack 文件写入安全工具。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PACK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
FILE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def validate_pack_identifier(value: str) -> str:
    """
    功能：校验 Story Pack 目录 ID，保持与 Web/OpenAPI 路径参数契约一致。
    入参：value（str）：manifest.pack_id 或删除目标 pack_id，必须为 3-64 位稳定标识符。
    出参：str，去除首尾空白后的合法 ID。
    异常：ValueError；为空、长度或字符集不符合稳定 ID 契约时抛出。
    """
    normalized = value.strip()
    if not PACK_ID_PATTERN.fullmatch(normalized):
        raise ValueError("pack_id 格式非法，必须为 3-64 位字母、数字、下划线或连字符")
    return normalized


def validate_file_identifier(value: str, field_name: str) -> str:
    """
    功能：校验 scenes/quests/triggers 的 JSON 文件 ID，避免 ID 参与路径穿越。
    入参：value（str）：对象内 scene_id、quest_id 或 trigger_id；
        field_name（str）：用于诊断的字段名。
    出参：str，去除首尾空白后的合法文件 stem。
    异常：ValueError；为空或包含路径分隔、点号、盘符等非稳定 ID 字符时抛出。
    """
    normalized = value.strip()
    if not FILE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 格式非法，必须只包含字母、数字、下划线或连字符")
    return normalized


def normalize_relative_content_name(raw_name: Any, field_name: str) -> str:
    """
    功能：校验 lore 等文本文件名是相对路径，并规范 Windows 反斜杠。
    入参：raw_name（Any）：上传或生成产物中的文件名；field_name（str）：诊断字段名。
    出参：str，使用 `/` 分隔的相对文件名。
    异常：ValueError；绝对路径、盘符、空路径、`.`、`..` 或空片段时抛出。
    """
    filename = str(raw_name).strip().replace("\\", "/")
    path = Path(filename)
    if (
        not filename
        or filename.startswith("/")
        or ":" in filename
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field_name} 文件名非法: {raw_name}")
    return "/".join(path.parts)


def resolve_pack_directory(root: Path, pack_id: str) -> Path:
    """
    功能：构造 pack 目录路径，并确认目录直接位于 registry 根目录下。
    入参：root（Path）：registry 根目录；pack_id（str）：已校验或待校验的 pack ID。
    出参：Path，解析后的目标目录。
    异常：ValueError；pack_id 非法或目标不在 registry 根目录的直属子目录时抛出。
    """
    safe_pack_id = validate_pack_identifier(pack_id)
    root_resolved = root.resolve()
    target = (root_resolved / safe_pack_id).resolve()
    if target.parent != root_resolved:
        raise ValueError("pack_id 路径非法")
    return target


def resolve_json_file_path(directory: Path, item_id: str, field_name: str) -> Path:
    """
    功能：根据稳定对象 ID 构造 JSON 文件路径，并确认不会越出目标集合目录。
    入参：directory（Path）：scenes/quests/triggers 目录；item_id（str）：对象稳定 ID；
        field_name（str）：诊断字段名。
    出参：Path，解析后的 `<item_id>.json` 路径。
    异常：ValueError；ID 非法或解析后不在集合目录直属层级时抛出。
    """
    safe_item_id = validate_file_identifier(item_id, field_name)
    directory_resolved = directory.resolve()
    target = (directory_resolved / f"{safe_item_id}.json").resolve()
    if target.parent != directory_resolved:
        raise ValueError(f"{field_name} 路径非法")
    return target


def resolve_relative_content_path(directory: Path, raw_name: Any, field_name: str) -> Path:
    """
    功能：根据相对文本文件名构造路径，并确认最终路径仍位于目标内容目录内。
    入参：directory（Path）：内容根目录，如 pack/lore；raw_name（Any）：相对文件名；
        field_name（str）：诊断字段名。
    出参：Path，解析后的目标文件路径。
    异常：ValueError；文件名非法或最终路径越界时抛出。
    """
    filename = normalize_relative_content_name(raw_name, field_name)
    directory_resolved = directory.resolve()
    target = (directory_resolved / filename).resolve()
    try:
        target.relative_to(directory_resolved)
    except ValueError as exc:
        raise ValueError(f"{field_name} 文件名非法: {raw_name}") from exc
    return target
