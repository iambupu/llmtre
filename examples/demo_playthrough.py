"""
A2-Release 外部示例 demo pack 试玩脚本。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEMO_PLAYTHROUGH_STEPS = [
    "观察雾林边缘",
    "沿旧路进入废弃营地",
    "呼唤巡林人艾拉",
    "翻看火坑灰烬",
    "沿石阶前往遗迹门",
    "询问石门旁的守门学者",
]


def build_demo_playthrough(
    pack_path: str | Path = "examples/story_packs/demo_a2_core",
) -> dict[str, Any]:
    """
    功能：校验外部示例 demo pack，并生成固定试玩步骤摘要。
    入参：pack_path（str | Path，默认 examples/story_packs/demo_a2_core）：待演示 pack 目录。
    出参：dict[str, Any]，包含 summary、start_scene_id 和 steps。
    异常：StoryPackValidationError；demo pack 校验失败时向上抛出，由 CLI 转为非 0 退出。
    """
    from tools.packs.registry import validate_story_pack

    bundle = validate_story_pack(pack_path)
    return {
        "pack_id": bundle.summary.pack_id,
        "title": bundle.summary.title,
        "version": bundle.summary.version,
        "start_scene_id": bundle.summary.start_scene_id,
        "compiled_artifact_hash": bundle.summary.compiled_artifact_hash,
        "scene_count": bundle.summary.scene_count,
        "interaction_count": bundle.summary.interaction_count,
        "quest_count": bundle.summary.quest_count,
        "trigger_count": bundle.summary.trigger_count,
        "steps": list(DEMO_PLAYTHROUGH_STEPS),
    }


def main() -> int:
    """
    功能：执行 A2-Release demo pack 试玩脚本入口，打印 JSON 或文本步骤。
    入参：命令行参数；--pack 指定 pack 目录，--json 输出 JSON。
    出参：int，0 表示脚本成功，校验失败时由异常导致非 0。
    异常：validate_story_pack 抛出的校验异常不在此捕获，保留给命令行调用者定位。
    """
    parser = argparse.ArgumentParser(description="Print the A2 demo pack playthrough script.")
    parser.add_argument("--pack", default="examples/story_packs/demo_a2_core")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = build_demo_playthrough(args.pack)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"A2 Demo Pack: {payload['title']} ({payload['pack_id']})")
    print(f"hash={payload['compiled_artifact_hash']} start={payload['start_scene_id']}")
    for index, step in enumerate(payload["steps"], start=1):
        print(f"{index}. {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
