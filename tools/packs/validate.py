"""
Story Pack 校验 CLI。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.packs.registry import StoryPackValidationError, validate_story_pack


def main() -> int:
    """
    功能：执行 Story Pack 校验命令并打印 JSON 摘要或诊断。
    入参：命令行参数，位置参数 pack_path 指向本地 pack 目录。
    出参：int，0 表示校验通过，1 表示校验失败。
    异常：不向外抛业务异常；校验失败转换为 JSON 诊断输出。
    """
    parser = argparse.ArgumentParser(description="Validate an A2 Story Pack.")
    parser.add_argument("pack_path", type=Path)
    args = parser.parse_args()
    try:
        bundle = validate_story_pack(args.pack_path)
    except StoryPackValidationError as error:
        print(json.dumps({"ok": False, "diagnostics": error.diagnostics}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "pack": bundle.summary.model_dump()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
