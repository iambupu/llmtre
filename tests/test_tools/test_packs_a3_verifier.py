"""
功能：覆盖 A3 golden Story Pack 分支验收工具。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tools.packs.verify_a3 import verify_a3_branching_pack


def test_a3_branching_golden_pack_passes() -> None:
    """
    功能：验证 A3 golden pack 同时满足通用 Story Pack 契约和分支任务验收门槛。
    入参：无。
    出参：None。
    异常：断言失败表示 golden pack 或 A3 校验器契约回归。
    """
    result = verify_a3_branching_pack(Path("examples/story_packs/a3_branching_quest"))

    assert result.ok, result.diagnostics
    assert result.pack_id == "a3_branching_quest"
    assert result.scene_count >= 3
    assert result.interaction_count >= 5
    assert len(result.branch_groups) == 1
    branch_group = result.branch_groups[0]
    assert branch_group.quest_id == "unmask_the_salt_deal"
    assert branch_group.branch_group == "salt_deal_approach"
    assert set(branch_group.branch_stage_ids) == {"report_to_watch", "strike_quay_bargain"}
    assert branch_group.merge_stage_id == "seal_the_evidence"


def test_a3_verifier_rejects_non_branching_demo_pack(tmp_path: Path) -> None:
    """
    功能：验证普通 A2 demo pack 不会被误判为 A3 分支验收包。
    入参：tmp_path（Path）：pytest 提供的临时目录。
    出参：None。
    异常：断言失败表示 A3 验收门槛过低。
    """
    target = tmp_path / "demo_a2_core"
    shutil.copytree("examples/story_packs/demo_a2_core", target)

    result = verify_a3_branching_pack(target)

    assert not result.ok
    assert "A3 golden pack 至少需要声明 1 个 a3_branch_group 分支组" in result.diagnostics
