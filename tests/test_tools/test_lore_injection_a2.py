"""
功能：覆盖 lore injection a2 的回归测试。
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from game_workflows.lore_loader import load_pack_context
from state.contracts.story_pack import (
    StoryPackBundle,
    StoryPackManifest,
    StoryPackSummary,
)


def _mk_summary(pid: str) -> StoryPackSummary:
    """构建测试用 StoryPackSummary，除 pack_id 外使用默认值。"""
    return StoryPackSummary(
        pack_id=pid,
        title="T",
        version="0.1.0",
        start_scene_id="s",
        start_scene_title="起始场景",
        compiled_artifact_hash="h",
        scene_count=0,
        interaction_count=0,
    )


def _manifest_json(pid: str, **extra: Any) -> str:
    """构建 manifest.json 的 JSON 字符串，extra 会合并到基础 dict。"""
    base = {
        "pack_id": pid,
        "version": "0.1.0",
        "title": "T",
        "start_scene_id": "s",
    }
    base.update(extra)
    return json.dumps(base, ensure_ascii=False)


def _root(name: str) -> Path:
    """
    功能：提供 root 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    r = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if r.exists():
        shutil.rmtree(r)
    r.mkdir(parents=True)
    return r


def _rm(r: Path) -> None:
    """
    功能：提供 rm 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    if r.exists():
        shutil.rmtree(r)


def _mkb(pid: str) -> StoryPackBundle:
    """
    功能：提供 mkb 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    m = StoryPackManifest(
        pack_id=pid,
        version="0.1.0",
        title="T",
        start_scene_id="s",
    )
    return StoryPackBundle(
        manifest=m,
        scenes={},
        quests={},
        triggers={},
        summary=_mk_summary(pid),
    )


def test_lore_files() -> None:
    """
    功能：验证 lore files 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    case = _root("lore")
    try:
        pd = case / "tp"
        pd.mkdir()
        mj = _manifest_json("tp", lore_files=["w.md"])
        (pd / "manifest.json").write_text(mj, encoding="utf-8")
        (pd / "w.md").write_text("hello world", encoding="utf-8")
        bm = StoryPackManifest(
            pack_id="tp",
            version="0.1.0",
            title="T",
            start_scene_id="s",
            lore_files=["w.md"],
        )
        b = StoryPackBundle(
            manifest=bm,
            scenes={},
            quests={},
            triggers={},
            summary=_mk_summary("tp"),
        )
        ctx = load_pack_context(b, case)
        assert "[Lore] w.md" in ctx
        assert "hello world" in ctx
    finally:
        _rm(case)


def test_empty() -> None:
    """
    功能：验证 empty 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    case = _root("empty")
    try:
        pd = case / "tp"
        pd.mkdir()
        mj = _manifest_json("tp", lore_files=[])
        (pd / "manifest.json").write_text(mj, encoding="utf-8")
        b = _mkb("tp")
        ctx = load_pack_context(b, case)
        assert ctx == ""
    finally:
        _rm(case)


def test_persona() -> None:
    """
    功能：验证 persona 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    case = _root("persona")
    try:
        pd = case / "tp"
        pd.mkdir()
        mj = _manifest_json("tp", lore_files=[])
        (pd / "manifest.json").write_text(mj, encoding="utf-8")
        per = pd / "persona"
        per.mkdir()
        (per / "alice.md").write_text("Alice here", encoding="utf-8")
        bm = StoryPackManifest(
            pack_id="tp",
            version="0.1.0",
            title="T",
            start_scene_id="s",
            lore_files=[],
        )
        b = StoryPackBundle(
            manifest=bm,
            scenes={},
            quests={},
            triggers={},
            summary=_mk_summary("tp"),
        )
        ctx = load_pack_context(b, case)
        assert "[Persona] alice.md" in ctx
        assert "Alice here" in ctx
    finally:
        _rm(case)


def test_missing_lore() -> None:
    """
    功能：验证 missing lore 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    case = _root("miss")
    try:
        pd = case / "tp"
        pd.mkdir()
        mj = _manifest_json("tp", lore_files=["missing.md"])
        (pd / "manifest.json").write_text(mj, encoding="utf-8")
        bm = StoryPackManifest(
            pack_id="tp",
            version="0.1.0",
            title="T",
            start_scene_id="s",
            lore_files=["missing.md"],
        )
        b = StoryPackBundle(
            manifest=bm,
            scenes={},
            quests={},
            triggers={},
            summary=_mk_summary("tp"),
        )
        ctx = load_pack_context(b, case)
        assert ctx == ""
    finally:
        _rm(case)


def test_demo() -> None:
    """
    功能：验证 demo 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    from tools.packs.registry import validate_story_pack

    b = validate_story_pack("examples/story_packs/demo_a2_core")
    ctx = load_pack_context(b, "examples/story_packs")
    assert "[Lore] world.md" in ctx
    assert "雾林边境" in ctx
